import os
import base64
import hashlib
import pathlib
import shutil
import zlib
import datetime
import logging

# TZLocal is set as a hidden import on win pipeline
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError
from tzlocal import get_localzone

from app.classes.shared.crypto_helper import CryptoHelper
from app.classes.shared.file_helpers import FileHelpers
from app.classes.shared.helpers import Helpers
from app.classes.models.management import HelpersManagement

# Set byte constants
BYTE_FALSE = bytes.fromhex("00")
BYTE_TRUE = bytes.fromhex("01")

logger = logging.getLogger(__name__)

try:
    TZ = get_localzone()
except ZoneInfoNotFoundError as e:
    logger.error(
        "Could not capture time zone from system. Falling back to Europe/London"
        f" error: {e}"
    )
    TZ = ZoneInfo("Europe/London")


class BackupManager:
    def __init__(self, server_instance, file_helper):
        self.server_instance = server_instance
        self.crypto_helper = CryptoHelper()
        self.file_helper: FileHelpers = file_helper

    ####################################################################################
    ##########################   SNAPSHOT METHODS ######################################
    ####################################################################################

    @staticmethod
    def blake2_hash_bytes(bytes_to_hash: bytes) -> bytes:
        """
        Hashes bytes with blake2b hash function. Returns hash in bytes format.
        :param bytes_to_hash: Bytes to hash
        :return: Hash of bytes in bytes.
        """
        blake2 = hashlib.blake2b()
        blake2.update(bytes_to_hash)
        return blake2.digest()

    @staticmethod
    def blake2_hash_file(file_path_to_file: pathlib.Path) -> bytes:
        """
        Reads file at path and creates blake2b hash for file.
        :param file_path_to_file: Pathlib.path to file to hash.
        :return: Hash of file in bytes.
        """
        blake2 = hashlib.blake2b()
        try:
            with file_path_to_file.open("rb") as file_to_hash:
                while True:
                    data = file_to_hash.read(64436)
                    if not data:
                        break
                    blake2.update(data)
        except (FileNotFoundError, PermissionError) as why:
            raise RuntimeError(f"Error accessing file: {file_path_to_file}") from why
        return blake2.digest()

    @staticmethod
    def bytes_to_b64(input_bytes: bytes) -> str:
        """
        Converts given input bytes into a b64 encoded string
        :param input_bytes: input bytes to convert to a string
        :return: b64 encoded string of bytes
        """
        return base64.b64encode(input_bytes).decode("UTF-8").rstrip("\n")

    @staticmethod
    def bytes_to_hex(input_bytes: bytes) -> str:
        """
        Converts given input bytes into a hex encoded string.
        :param input_bytes: Bytes to convert to a hex string.
        :return: hex encoded string of bytes.
        """
        return input_bytes.hex()

    @staticmethod
    def compress_bytes(bytes_to_compress: bytes) -> bytes:
        """
        Compress given bytes with zlib compression.
        :param bytes_to_compress: Bytes to compress
        :return: Zlib compressed bytes
        """
        return zlib.compress(bytes_to_compress)

    @staticmethod
    def decompress_bytes(input_bytes: bytes) -> bytes:
        """
        Decompress given bytes with zlib compression.
        May throw zlib.Error if bytes are not compressed with zlib.
        :param input_bytes: Bytes to decompress.
        :return: Decompressed bytes.
        """
        return zlib.decompress(input_bytes)

    @staticmethod
    def discover_files(target: pathlib.Path) -> list:
        """
        Finds all files in given target directory and returned list of paths to
        those files.
        :param target: Path object to directory to explore.
        :return: List of paths to files in target directory.
        """
        # Check that target is a directory
        if not target.is_dir():
            raise NotADirectoryError(f"{target} is not a directory")

        discovered_files = []

        # Use pathlib's rglob to get all files
        for p in target.rglob("*"):
            if p.is_file():
                discovered_files.append(p)

        return discovered_files

    @staticmethod
    def get_local_path_with_base(desired_path: pathlib.Path, base: pathlib.Path) -> str:
        """
        Removes base from given path.
        Given:
            Path: /root/example.md
            Base: /root/
            Returns: example.md
        :param desired_path: Path to target file.
        :param base: Base path to remove from full path.
        :return: Path with base removed.
        """
        # Check length of path is longer than desired base.
        len_path = len(str(desired_path.absolute()))
        len_base = len(str(base.absolute()))
        if len_base >= len_path:
            raise ValueError(
                f"Path is too small: Path {desired_path} is longer than base {base}!"
            )

        # Check that path is contained in base.
        if base not in desired_path.parents:
            raise ValueError(f"{desired_path} is not a child of {base}.")

        # Check that base is a directory.
        if not base.is_dir():
            raise NotADirectoryError(f"{base} is not a directory.")

        # Return path with base removed.
        return str(desired_path)[len_base + 1 :]

    def get_chunk_path_from_hash(
        self, chunk_hash: bytes, repository_location: pathlib.Path
    ) -> pathlib.Path:
        """
        Given chunk hash and repository location, gets full path to chunk file.
        :param chunk_hash: Hash of chunk.
        :param repository_location: Base repository location.
        :return: Path object of chunk path.
        """
        hash_hex = self.bytes_to_hex(chunk_hash)
        return repository_location / "chunks" / hash_hex[:2] / hash_hex[-126:]

    def get_file_path_from_hash(
        self, file_hash: bytes, repository_location: pathlib.Path
    ) -> pathlib.Path:
        """
        Given chunk hash and repository location, gets full path to chunk file.
        :param file_hash: Hash of chunk.
        :param repository_location: Base repository location.
        :return: Path object of chunk path.
        """
        hash_hex = self.bytes_to_hex(file_hash)
        return repository_location / "files" / hash_hex[:2] / hash_hex[-126:]

    @staticmethod
    def initialize_backup_repository(repository_path: pathlib.Path) -> None:
        """
        Initializes backup repository. Create readme and folders.
        :param repository_path: Path to the backup repository.
        :return: None
        """
        # Ensure that repository path is a directory.
        if not repository_path.is_dir():
            raise NotADirectoryError(f"{repository_path} is not a directory")

        # Create readme if not already there.
        readme_location = repository_path / "README.md"
        if not readme_location.exists():
            try:
                with readme_location.open("w+") as file:
                    file.write("Managed backup repository. Do not edit files!")
            except PermissionError as why:
                raise RuntimeError(
                    f"Error writing to repository location at {repository_path}"
                ) from why

    def save_chunk(
        self,
        file_chunk: bytes,
        repository_location: pathlib.Path,
        chunk_hash: bytes,
        use_compression: bool,
    ) -> None:
        """
        Saves chunk bytes to repository location.
        :param file_chunk: Bytes to save to file.
        :param repository_location: Path to base repository location.
        :param chunk_hash: Hash of chunk to save.
        :param use_compression: Boolean of if compression should be used to save chunk.
        :return: None
        """
        # Get file location and save to location
        file_location = self.get_chunk_path_from_hash(chunk_hash, repository_location)

        # Exit if chunk is already saved.
        if file_location.exists():
            return
        file_location.parent.mkdir(parents=True, exist_ok=True)

        # Chunk schema version
        version = bytes.fromhex("00")

        # Compress bytes if set to true
        if use_compression:
            file_chunk = self.compress_bytes(file_chunk)
            compression = BYTE_TRUE
        else:
            compression = BYTE_FALSE

        # Placeholder for encryption
        encryption = BYTE_FALSE
        nonce = bytes.fromhex("000000000000000000000000")

        # Create Chunk
        output = version + encryption + nonce + compression + file_chunk

        # Save chunk
        try:
            with file_location.open("wb+") as file:
                file.write(output)
        except PermissionError as why:
            raise PermissionError(
                f"Unable to write to location {file_location}"
            ) from why

    def save_file(
        self,
        source_file: pathlib.Path,
        repository_location: pathlib.Path,
        file_hash: bytes,
        use_compression: bool,
    ) -> None:
        """
        Save file to repository location. Splits file into 20 mb chunks to save memory.
        Chunks are written to a file manifest file in the repository.
        :param source_file: Path to source file to save.
        :param repository_location: Path to repository location.
        :param file_hash: Hash of file as bytes.
        :param use_compression: If True, compression will be used to save the chunk.
        :return: None
        """
        # Read file in 20 mb chunks while writing to chunks file.
        file_manifest_file_location = self.get_file_path_from_hash(
            file_hash, repository_location
        )

        # Exit if file is already present.
        if file_manifest_file_location.exists():
            return

        # Create containing folder in repository.
        file_manifest_file_location.parent.mkdir(parents=True, exist_ok=True)

        # Open file and start saving chunks.
        # Source file.
        try:
            source_file_obj = source_file.open("rb")
        except (FileNotFoundError, PermissionError) as why:
            raise RuntimeError(f"Unable to read file {source_file}.") from why

        # Target file manifest file.from
        try:
            file_manifest_file = file_manifest_file_location.open("w+")
        except PermissionError as why:
            # Close other file before throwing error.
            source_file_obj.close()
            raise RuntimeError(
                f"Unable to write file {file_manifest_file_location}."
            ) from why

        # Loop over source file in chunks to limit memory use.
        while True:
            # Read in 20 MB chunks
            chunk = source_file_obj.read(20_000_000)

            # Exit if at end of file.
            if not chunk:
                source_file_obj.close()
                file_manifest_file.close()
                return

            # Find chunk hash and write to file manifest.
            chunk_hash = self.blake2_hash_bytes(chunk)
            chunk_hash_as_b64 = self.bytes_to_hex(chunk_hash)
            file_manifest_file.write(chunk_hash_as_b64 + "\n")

            # Save Chunk to storage
            self.save_chunk(chunk, repository_location, chunk_hash, use_compression)

    @staticmethod
    def str_to_b64(input_str: str) -> str:
        """
        Given source string, converts to base64 encoded string.
        :param input_str: String to convert.
        :return: b64 encoded string.
        """
        return base64.b64encode(input_str.encode("utf-8")).decode("utf-8").rstrip("\n")

    @staticmethod
    def b64_to_bytes(input_b64: str) -> bytes:
        """
        Converts b64 encoded string to bytes.
        :param input_b64: Base 64 encoded string.
        :return: Encoded bytes, decoded.
        """
        return base64.b64decode(input_b64.encode("utf-8"))

    def b64_to_str(self, input_b64: str) -> str:
        """
        Converts b64 encoded string to string.
        :param input_b64: Base64 encoded string.
        :return: Decoded string.
        """
        return self.b64_to_bytes(input_b64).decode("utf-8")

    def snapshot(self, conf: dict) -> None:
        """
        Perform the backup.
        Iterate over files in source dir. Apply save function.
        Save file information to manifest.
        :return: UUID of backup.
        """
        # Initialize backup stuff.\
        backup_id = str(
            datetime.datetime.now()
            .astimezone(self.server_instance.tz)
            .strftime("%Y-%m-%d_%H-%M-%S")
        )
        source_path = pathlib.Path(self.server_instance.server_path)
        repo_path = pathlib.Path(conf["backup_location"]) / "snapshots"
        manifest_path = repo_path / "manifests" / f"{str(backup_id)}.manifest"

        # Get list of files to backup.
        list_of_files = self.discover_files(source_path)

        # Create manifest folder location
        manifest_path.parent.mkdir(parents=True, exist_ok=True)

        # Open manifest file
        try:
            manifest_file = manifest_path.open("w+")
        except PermissionError as why:
            raise RuntimeError(
                f"Unable to write to manifest file {manifest_path}."
            ) from why

        # Iterate over files in source location.
        for f in list_of_files:
            # Get hash of file.
            try:
                file_hash = self.blake2_hash_file(f)
            except (FileNotFoundError, PermissionError) as why:
                manifest_file.close()
                raise RuntimeError(f"Unable to read file {f}.") from why

            # Save file
            try:
                self.save_file(f, repo_path, file_hash, True)
            except RuntimeError as why:
                manifest_file.close()
                raise RuntimeError(f"Unable to save file at {f}") from why

            # Get local path of saved file compared to base.
            file_local_path = self.get_local_path_with_base(f, source_path)

            # Save file to manifest
            manifest_file.write(
                f"{self.bytes_to_b64(file_hash)}:{self.str_to_b64(file_local_path)}\n"
            )

        # Backup complete
        manifest_file.close()

    def recover(
        self,
        source_manifest_path: pathlib.Path,
        destination_path: pathlib.Path,
        backup_repo_path: pathlib.Path,
    ) -> None:
        """
        Restore files from backup.
        :param source_manifest_path: Path to manifest file to restore.
        :param destination_path: Destination of restored files.
        :param backup_repo_path: Path to base repository location.
        :return: None
        """
        # Remove files if destination is not empty.
        if destination_path.exists():
            # Ensure destination is empty
            shutil.rmtree(destination_path)

        # Make folder for restore to
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        # Open backup manifest
        try:
            backup_manifest_file = source_manifest_path.open("r")
        except PermissionError as why:
            raise RuntimeError(
                f"Unable to read manifest file {source_manifest_path}."
            ) from why

        for file_hash_and_path in backup_manifest_file:
            hash_and_local_path = file_hash_and_path.split(":")
            file_hash = self.b64_to_bytes(hash_and_local_path[0])
            recovered_file_path = pathlib.Path(
                destination_path, self.b64_to_str(hash_and_local_path[1])
            ).resolve()

            # Recover file
            try:
                self.recover_file(file_hash, recovered_file_path, backup_repo_path)
            except (RuntimeError, FileNotFoundError) as why:
                raise RuntimeError(
                    f"Unable to recover file at {file_hash_and_path}"
                ) from why

    def recover_file(
        self,
        file_hash: bytes,
        target_path: pathlib.Path,
        backup_repo_path: pathlib.Path,
    ) -> None:
        """
        Restore files from backup based on file hash.
        :param file_hash: Hash of file to restore.
        :param target_path: Destination of recovered files.
        :param backup_repo_path: Path to backup repository.
        :return: None
        """
        # Get file manifest file path
        source_file_manifest_path = self.get_file_path_from_hash(
            file_hash, backup_repo_path
        )

        # Ensure file is present
        if not source_file_manifest_path.exists():
            raise FileNotFoundError(
                f"File with hash {self.bytes_to_hex(file_hash)} not found in repository."
            )

        # Create path for file
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Open target path
        try:
            target_file = target_path.open("wb+")
        except PermissionError as why:
            raise RuntimeError(
                f"Permission denied when trying to open path: {target_path}"
            ) from why

        try:
            source_file_manifest = source_file_manifest_path.open("r")
        except (FileNotFoundError, PermissionError) as why:
            target_file.close()
            raise RuntimeError(
                f"Unable to read file {source_file_manifest_path}."
            ) from why

        # Iterate over chunks in file manifest and write them to target file.
        for line in source_file_manifest:
            chunk_hash = bytes.fromhex(line)
            try:
                target_file.write(self.read_chunk(chunk_hash, backup_repo_path))
            except RuntimeError as why:
                raise RuntimeError(
                    f"Unable to read chunk of hash {self.bytes_to_hex(chunk_hash)}"
                ) from why

        target_file.close()
        source_file_manifest.close()

    def read_chunk(self, chunk_hash: bytes, repo_path: pathlib.Path) -> bytes:
        """
        Reads chunk out of backup repository. Handles decompression and (soon) decryption.
        :param chunk_hash: Hash of chunk to read.
        :param repo_path: Path  the backup repository.
        :return: Read bytes from repository.
        """
        # Get chunk path
        chunk_path = self.get_chunk_path_from_hash(chunk_hash, repo_path)

        # Attempt to read chunk
        try:
            chunk_file = chunk_path.open("rb")
        except (FileNotFoundError, PermissionError) as why:
            raise RuntimeError(f"Unable to read file {chunk_path}.") from why

        # Confirm first byte is expected version
        version = chunk_file.read(1)
        if version != bytes.fromhex("00"):
            raise RuntimeError(
                (
                    "Chunk is of unexpected version. "
                    f"Unable to read. Version was {self.bytes_to_hex(version)}."
                )
            )

        # Read encryption byte and nonce. Code not currently used.
        # One byte for use encryption byte and 12 bytes nonce.
        _ = chunk_file.read(13)

        # Read use compression byte
        use_compression_byte = chunk_file.read(1)

        if use_compression_byte == BYTE_TRUE:
            try:
                return self.decompress_bytes(chunk_file.read())
            except zlib.error as why:
                raise RuntimeError(f"Unable to decompress file {chunk_path}.") from why
        else:
            return chunk_file.read()

    ####################################################################################
    ##########################   LEGACY BACKUP METHODS #################################
    ####################################################################################
    def restore_backup(self, archive_source: str, restore_dest: str, in_place=True):
        """takes backup config and archive source and restores it to desired location in
        the server location.

        Args:
            archive_source (str): location of backup archive
            restore_dest (str): destination for backup archive
            in_place (str): Whether we should wipe the directory or just
            replace the files in the backup
        """
        if (
            not in_place
        ):  # If user does not want to backup in place we will clean the server dir
            for item in os.listdir(restore_dest):
                if os.path.isdir(os.path.join(restore_dest, item)):
                    self.file_helper.del_dirs(os.path.join(restore_dest, item))
                else:
                    self.file_helper.del_file(os.path.join(restore_dest, item))
        self.file_helper.restore_archive(archive_source, restore_dest)

    def make_backup(self, conf, backup_location):
        backup_filename = (
            f"{backup_location}/"
            f"{datetime.datetime.now().astimezone(TZ).strftime('%Y-%m-%d_%H-%M-%S')}"  # pylint: disable=line-too-long
        )
        logger.info(
            f"Creating backup of server {conf['server_id']['server_name']}"
            f" (ID#{conf['server_id']['server_id']}, path={conf['server_id']['path']}) "
            f"at '{backup_filename}'"
        )
        excluded_dirs = HelpersManagement.get_excluded_backup_dirs(conf["backup_id"])
        server_dir = Helpers.get_os_understandable_path(conf["server_id"]["path"])

        self.file_helper.make_backup(
            Helpers.get_os_understandable_path(backup_filename),
            server_dir,
            excluded_dirs,
            conf["server_id"]["server_id"],
            conf["backup_id"],
            conf["backup_name"],
            conf["compress"],
        )
