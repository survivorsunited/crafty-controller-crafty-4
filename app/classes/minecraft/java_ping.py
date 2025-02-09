from collections import namedtuple
import logging
import socket
import struct
import typing

logger = logging.getLogger(__name__)
VarInt = namedtuple("VarInt", ["value", "bytes"])
FieldMeta = namedtuple("FieldMeta", ["bytes", "signed", "varlen"])


class JavaPing:
    field_sizes = {  # (len, signed)
        "byte": FieldMeta(bytes=1, signed=False, varlen=""),
        "ubyte": FieldMeta(bytes=1, signed=True, varlen=""),  # unsigned byte
        "long": FieldMeta(bytes=8, signed=True, varlen=""),
        "short": FieldMeta(bytes=2, signed=True, varlen=""),
        "ushort": FieldMeta(bytes=2, signed=False, varlen=""),  # unsigned short
        "string": FieldMeta(
            bytes=2, signed=False, varlen="var_int"
        ),  # strlen is varint
        "bool": FieldMeta(bytes=1, signed=False, varlen=""),
        "var_int": FieldMeta(bytes=5, signed=True, varlen=""),
        "var_long": FieldMeta(bytes=10, signed=True, varlen=""),
    }
    byte_order = "big"

    def __init__(self, addr: str, port: int):
        self.addr = addr
        self.port = port

    @staticmethod
    def __unpack_var_int(buffer: bytes, max_bytes: int = 5) -> VarInt:
        # https://minecraft.wiki/w/Minecraft_Wiki:Projects/wiki.vg_merge/Data_types#Type:VarInt
        final_value = 0
        byte_index = 0
        while True:
            try:
                k = buffer[byte_index]
            except IndexError as e:
                raise ValueError from e
            final_value |= (k & 0x7F) << (byte_index * 7)
            byte_index += 1
            if byte_index > max_bytes:
                raise ValueError("var_int too big")
            if not k & 0x80:
                return VarInt(value=final_value, bytes=byte_index)

    def ping(self) -> typing.Union[dict, None]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect((self.addr, self.port))

        except ConnectionRefusedError:
            logger.debug(
                "Connection refused while pinging server at %s:%i", self.addr, self.port
            )
            return {}
        except TimeoutError:
            logger.debug("Timeout while pinging server at %s:%i", self.addr, self.port)
            return None
        except (socket.herror, socket.gaierror) as e:
            logger.error(
                "Minecraft ping encountered an address resolution issue with %s: %s",
                self.addr,
                e,
            )
            return None

        try:
            host = self.addr.encode("utf-8")
            data = b""  # wiki.vg/Server_List_Ping
            data += b"\x00"  # packet ID
            data += b"\x04"  # protocol variant
            data += struct.pack(">b", len(host)) + host
            data += struct.pack(">H", self.port)
            data += b"\x01"  # next state
            data = struct.pack(">b", len(data)) + data
            sock.sendall(data + b"\x01\x00")  # handshake + status ping
            buffer = sock.recv(1024)
            buffer_index = 0
            packet_length = JavaPing.__unpack_var_int(
                buffer[buffer_index:], max_bytes=5
            )  # full packet length
            if packet_length.value < 10:
                return None
            buffer_index += packet_length.bytes

            # get packet type, 0 for pings
            packet_type = buffer[buffer_index]
            if packet_type != 0x0:
                err_msg = (
                    "Incorrect Minecraft Java packet type on ping response, "
                    + f"expecting 0x00, got 0x{packet_type:02x}"
                )
                raise ValueError(err_msg)
            buffer_index += 1
            data_length = JavaPing.__unpack_var_int(
                buffer[buffer_index:], max_bytes=3
            )  # string length
            buffer_index += data_length.bytes
            try:
                data = buffer[buffer_index : buffer_index + data_length.value]
            except IndexError:
                logger.exception(
                    "Packet buffer too short while parsing Java ping, "
                    "expected %i, found %i"
                )
                logger.debug("Java packet buffer at the time of exception: %s", buffer)
                return None
            logger.debug(f"Server reports this data on ping: {data}")
            return data
        finally:
            sock.close()
