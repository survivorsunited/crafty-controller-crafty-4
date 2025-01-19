import json
import logging
from datetime import datetime
import requests

logger = logging.getLogger(__name__)


class SteamApps:
    def __init__(self, helper):
        self.helper = helper

    ############################################
    #####  Dedicated Server List Retrival  #####
    ############################################

    def _get_dedicated_server_list(self, sort_by="name"):
        """Fetches a list of all Steam apps and filters apps with 'server' in their name.

        This repository stores every dedicated server AppID and its name available
        on Steam by grabbing the info from the SteamAPI and filtering for the word
        'server'. Remote Data refreshes on "0 0 * * *"

        Args:
            sort_by (str): The key to sort the results by. Can be 'appid' or 'name'.

        Returns:
            list:
                {
                    "appid": (int),
                    "name": name of dedicated server(str)
                }
        """
        steam_applist_url = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
        
        try:
            response = requests.get(steam_applist_url)
            response.raise_for_status()  # Raise an exception for HTTP errors
            app_list = response.json()

            # Filter apps with 'server' in their name (case insensitive)
            server_apps = [
                {"appid": app["appid"], "name": app["name"]}
                for app in app_list["applist"]["apps"]
                if "server" in app["name"].lower()
            ]

            # Remove duplicates by converting to a dictionary keyed by appid, then back to a list
            unique_apps = {app["appid"]: app for app in server_apps}.values()

            # Sort the unique apps
            if sort_by in {"appid", "name"}:
                unique_apps = sorted(unique_apps, key=lambda x: x[sort_by])

            return list(unique_apps)

        except requests.exceptions.RequestException as err:
            logger.error(f"AppList json not found on repository, Reason: {err}")
            return []

    ############################
    ##### CACHE MANAGEMENT #####
    ############################

    def fetch_cache(self):
        """Fetch SteamApps Cache

            Fetches local copy of the SteamApps dict list

        Returns:
            list:
                {
                    "appid": (int),
                    "subscriptionlinux": release status?(str),
                    "linux": (bool),
                    "subscriptionwindows": release status?(str),
                    "windows": (bool),
                    "name": name of dedicated server(str)
                }
        """
        cache_path = self.helper.steamapps_cache
        cache = []
        try:
            with open(cache_path, "r", encoding="utf-8") as cache_file:
                cache = json.load(cache_file)["steam_apps"]

        except Exception as e:
            logger.error(f"Unable to read SteamApps cache file: {e}")

        return cache

    def refresh_cache(self, force=False):
        """Refresh local SteamApps cache file

        Args:
            force (bool, optional): Override to force refresh cache file
            regardless of age.
                                                Defaults to False.

        Returns:
            refreshed? (bool): Wither or not the status file was refreshed
        """
        cache_path = self.helper.steamapps_cache
        app_list = self._get_dedicated_server_list()

        # If SteamApps retrival fails, bail to preserve existing cache
        if not app_list:
            return False

        logger.info("Checking Cache file age")
        cache_old = self.helper.is_file_older_than_x_days(cache_path)

        if cache_old or force:
            log_statement = "file is over 1 day old"
            if force:
                log_statement = "refresh forced"

            logger.info(f"Cache {log_statement}, refreshing")
            now = datetime.now()
            data = {
                "last_refreshed": now.strftime("%m/%d/%Y, %H:%M:%S"),
                "steam_apps": app_list,
            }

            # Save our cache
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(json.dumps(data, indent=4))
                    logger.info("SteamApps Cache file refreshed")
                    return True

            except Exception as e:
                logger.error(f"Unable to update SteamApps cache file: {e}")

        return False
