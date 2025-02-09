import base64
import json
import os
import re
import logging.config
import uuid
import random
import typing

from app.classes.minecraft.bedrock_ping import BedrockPing
from app.classes.minecraft.java_ping import JavaPing
from app.classes.shared.console import Console

logger = logging.getLogger(__name__)
MOTD_CODES = ["bold", "italic", "underlined", "strikethrough"]


class Server:
    def __init__(self, data):
        if isinstance(data, str):
            logger.error(
                "Failed to calculate stats. Expected object. "
                f"Server returned string: {data}"
            )
            return
        self.description = data.get("description")
        # print(self.description)
        if isinstance(self.description, dict):
            # cat server
            if "translate" in self.description:
                self.description = self.description["translate"]

            # waterfall / bungee
            elif "extra" in self.description:
                lines = []

                description = self.description
                if "text" in description.keys():
                    lines.append(description["text"])
                if "extra" in description.keys():
                    if isinstance(description["extra"], list):
                        for e in description["extra"]:
                            if not isinstance(e, dict):
                                lines.append(e)
                                continue
                            # Conversion format code needed only for Java Version
                            lines.append(get_code_format("reset"))
                            for item in MOTD_CODES:
                                if e.get(item, False):
                                    lines.append(get_code_format(item))
                            if "color" in e.keys():
                                lines.append(get_code_format(e["color"]))
                            # Then append the text
                            if "text" in e.keys():
                                if e["text"] == "\n":
                                    lines.append("§§")
                                else:
                                    lines.append(e["text"])

                total_text = " ".join(lines)
                self.description = total_text

            # normal MC
            else:
                self.description = self.description["text"]

        self.icon = base64.b64decode(data.get("favicon", "")[22:])
        try:
            self.players = Players(data["players"]).report()
        except KeyError:
            logger.error("Error geting player information key error")
            self.players = []
        self.version = data["version"]["name"]
        self.protocol = data["version"]["protocol"]


class Players(list):
    def __init__(self, data):
        super().__init__(Player(x) for x in data.get("sample", []))
        self.max = data.get("max", 0)
        self.online = data.get("online", 0)

    def report(self):
        players = []

        for player in self:
            players.append(str(player))

        r_data = {"online": self.online, "max": self.max, "players": players}

        return json.dumps(r_data)


class Player:
    def __init__(self, data):
        self.id = data.get("id", "")
        self.name = data.get("name", "Anonymous")

    def __str__(self):
        return self.name


def get_code_format(format_name):
    root_dir = os.path.abspath(os.path.curdir)
    format_file = os.path.join(root_dir, "app", "config", "motd_format.json")
    try:
        with open(format_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if format_name in data.keys():
            return data.get(format_name)
        logger.error(f"Format MOTD Error: format name {format_name} does not exist")
        Console.error(f"Format MOTD Error: format name {format_name} does not exist")
        return ""

    except Exception as e:
        logger.critical(f"Config File Error: Unable to read {format_file} due to {e}")
        Console.critical(f"Config File Error: Unable to read {format_file} due to {e}")

    return ""


# For the rest of requests see wiki.vg/Protocol
def ping_java(ip: str, port: int) -> typing.Union[Server, None]:
    try:
        j_ping = JavaPing(ip, port)
        j_ping_resp = j_ping.ping()
        if j_ping_resp:
            try:
                return Server(json.loads(j_ping_resp))
            except (KeyError, json.decoder.JSONDecodeError):
                return None
        else:
            return None
    except:
        logger.exception("Unable to get Java ping stats")


# For the rest of requests see wiki.vg/Protocol
def ping_bedrock(ip: str, port: int) -> dict:
    rand = random.Random()
    try:
        # pylint: disable=consider-using-f-string
        rand.seed("".join(re.findall("..", "%012x" % uuid.getnode())))
        client_guid = uuid.UUID(int=rand.getrandbits(32)).int
    except:
        client_guid = 0
    try:
        brp = BedrockPing(ip, port, client_guid)
        return brp.ping()
    except:
        logger.exception("Unable to get Bedrock ping stats")
