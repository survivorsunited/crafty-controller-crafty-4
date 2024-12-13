from app.classes.shared.crypto_helper import CryptoHelper


class BackupManager:
    def __init__(self, server_instance):
        self.server_instance = server_instance
        self.crypto_helper = CryptoHelper()
