import os
from pathlib import Path
import sys
import json
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QFileDialog, QLineEdit, QMessageBox, QTextEdit, QSlider
)
from PyQt5.QtCore import Qt, QUrl, QTimer, QThread, pyqtSignal, QObject
from PyQt5.QtGui import QPixmap
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from PyQt5.QtWidgets import QInputDialog
from PyQt5.QtWidgets import QInputDialog

from telethon import TelegramClient, functions
from telethon.tl.functions.channels import CreateChannelRequest, UpdateUsernameRequest, GetFullChannelRequest, EditPhotoRequest
from telethon.errors import ChannelInvalidError, UsernameOccupiedError, UserAlreadyParticipantError
from telethon.tl.functions.messages import ImportChatInviteRequest
import asyncio
import unicodedata
import threading
import builtins
import sys
import traceback
from bs4 import BeautifulSoup
import re

# === Chemin du fichier de configuration des clés API ===
API_KEYS_FILE = os.path.join(os.path.expanduser("~"), ".api_keys.json")

# === Chargement des clés API ===
def load_api_keys():
    if os.path.exists(API_KEYS_FILE):
        with open(API_KEYS_FILE, "r") as f:
            return json.load(f)
    return []

def save_api_keys(keys):
    with open(API_KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)

def extraire_numero(base_name):
    # Chercher à la fin : un ou plusieurs groupes de chiffres séparés par _ ou -
    match = re.search(r'(\d+(?:[_\-]\d+)*)$', base_name)
    if match:
        return match.group(1).replace("-", "_")
    return base_name

async def get_channel_entity(client, channel_link, logger):
    """
    Récupère l'entité Telegram d'un canal (public ou privé) de manière robuste.
    - Si canal public → get_entity
    - Si canal privé avec +code → get_entity si déjà membre/admin, sinon ImportChatInviteRequest
    """
    entity = None
    try:
        if not channel_link:
            raise ValueError("Aucun lien fourni")

        logger.log(f"🔗 Recherche du canal : {channel_link}")

        # Extraire suffixe (username ou +code)
        m = re.match(r"^https:\/\/t\.me\/(.+)$", channel_link)
        suffix = m.group(1) if m else channel_link

        if suffix.startswith("+"):
            invite_code = suffix[1:]

            # ✅ 1. Essayer directement get_entity (si déjà admin ou membre, ça marche)
            try:
                entity = await client.get_entity(suffix)
                logger.log("✅ Canal privé trouvé via get_entity (déjà membre/admin).")
            except Exception as e1:
                logger.log(f"⚠️ get_entity a échoué pour lien privé : {e1}")

                # ✅ 2. Si pas déjà participant → ImportChatInviteRequest
                try:
                    logger.log("🔑 Tentative d'import via code d'invitation...")
                    result = await client(ImportChatInviteRequest(invite_code))
                    entity = result.chats[0]
                    logger.log("✅ Canal privé rejoint via ImportChatInviteRequest.")
                except UserAlreadyParticipantError:
                    logger.log("ℹ️ Déjà membre du canal privé, récupération via get_entity forcée.")
                    entity = await client.get_entity(suffix)
                except Exception as e2:
                    logger.log(f"❌ Échec de l'import d'invitation privée : {e2}")
                    entity = None
        else:
            # ✅ Canal public classique
            entity = await client.get_entity(suffix)
            logger.log("✅ Canal public trouvé via get_entity.")

    except Exception as e:
        logger.log(f"📢 Canal non trouvé ou lien invalide : {e}")
        entity = None

    return entity

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    print("❌ Exception non interceptée :", exc_value)
    traceback.print_tb(exc_traceback)

sys.excepthook = handle_exception

# === Classe pour la gestion des logs ===
class Logger(QObject):
    log_signal = pyqtSignal(str)

    def __init__(self, log_widget):
        super().__init__()
        self.log_widget = log_widget
        self.log_signal.connect(self._append_text)

    def _append_text(self, text):
        self.log_widget.append(text)

    def log(self, text):
        self.log_signal.emit(text)

# === Classe pour le thread de publication Telegram ===
class TelegramWorker(QThread):
    finished = pyqtSignal()
    log_signal = pyqtSignal(str)

    def __init__(self, parent, api_name, api_id, api_hash, channel_title, channel_link, username, hashtag, hashtag_nom):
        super().__init__(parent)
        self.parent = parent
        self.api_name = api_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.channel_title = channel_title
        self.channel_link = channel_link
        self.username = username
        self.hashtag = hashtag
        self.hashtag_nom = hashtag_nom

    def run(self):
        try:
            asyncio.run(self.parent._send_telegram_async(
                self.api_name, self.api_id, self.api_hash,
                self.channel_title, self.channel_link, self.username, self.hashtag, self.hashtag_nom
            ))
        except Exception as e:
            print(f"Erreur dans le thread : {e}")
        self.finished.emit()

class BookUploader(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Telegram Course Publisher")
        self.resize(1200, 700)
        self.api_keys = load_api_keys()
        self.books_dir = ""
        self.books = []
        self.current_book = None
        self.current_index = 0
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("background-color: black; color: lime; font-family: monospace;")
        self.logger = Logger(self.log_output)

        self.setup_ui()

    def setup_ui(self):
        main_layout = QHBoxLayout()
        left_layout = QVBoxLayout()

        # Ligne API Key
        api_layout = QHBoxLayout()
        self.api_selector = QComboBox()
        self.refresh_api_selector()
        self.api_selector.currentIndexChanged.connect(self.select_api_key)
        api_layout.addWidget(QLabel("Identifiants API :"))
        api_layout.addWidget(self.api_selector)
        self.add_api_button = QPushButton("Ajouter une clé API")
        self.add_api_button.clicked.connect(self.add_api_key)
        api_layout.addWidget(self.add_api_button)
        left_layout.addLayout(api_layout)

        # Sélecteur de dossier de livres
        folder_layout = QHBoxLayout()
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("Sélectionner le dossier contenant les livres...")
        self.folder_button = QPushButton("Parcourir")
        self.folder_button.clicked.connect(self.select_books_folder)
        folder_layout.addWidget(self.folder_input)
        folder_layout.addWidget(self.folder_button)
        left_layout.addLayout(folder_layout)

        # Sélecteur de livre + nom canal
        book_layout = QHBoxLayout()
        self.book_selector = QComboBox()
        self.book_selector.currentIndexChanged.connect(self.update_book_preview)
        self.book_selector.currentIndexChanged.connect(self.play_media)
        self.channel_input = QLineEdit()
        self.channel_link_input = QLineEdit()
        book_layout.addWidget(QLabel("Livre :"))
        book_layout.addWidget(self.book_selector)
        book_layout.addWidget(QLabel("Nom du canal :"))
        book_layout.addWidget(self.channel_input)
        book_layout.addWidget(QLabel("Lien du canal :"))
        book_layout.addWidget(self.channel_link_input)
        left_layout.addLayout(book_layout)

        # Bouton pour sélectionner une image de profil
        self.channel_photo_path = None
        self.btn_select_photo = QPushButton("📷 Choisir une photo du canal")
        self.btn_select_photo.clicked.connect(self.select_channel_photo)
        photo_hashtag_layout = QHBoxLayout()
        photo_hashtag_layout.addWidget(self.btn_select_photo)

        self.hashtag_input = QLineEdit("#dars")
        self.hashtag_input.setMaximumWidth(100)
        photo_hashtag_layout.addWidget(QLabel("Hashtag:"))
        photo_hashtag_layout.addWidget(self.hashtag_input)
        
        self.main_channel_input = QLineEdit("majalisur_rahman")
        photo_hashtag_layout.addWidget(QLabel("ID Canal de Menu :"))
        photo_hashtag_layout.addWidget(self.main_channel_input)

        left_layout.addLayout(photo_hashtag_layout)

        # Visualiseur
        self.image_label = QLabel("[Aperçu image]")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setFixedHeight(300)
        left_layout.addWidget(self.image_label)

        self.player = QMediaPlayer()
        self.audio_slider = QSlider(Qt.Horizontal)
        self.audio_slider.setRange(0, 100)
        self.audio_slider.sliderMoved.connect(self.set_position)
        self.player.positionChanged.connect(self.update_slider)
        self.player.durationChanged.connect(self.update_duration)

        left_layout.addWidget(self.audio_slider)

        # Contrôles
        controls = QHBoxLayout()
        self.prev_button = QPushButton("⟸ Préc")
        self.play_button = QPushButton("▶")
        self.next_button = QPushButton("Suiv ⟹")
        self.speed_minus = QPushButton("v-5")
        self.speed_plus = QPushButton("v+5")

        self.prev_button.clicked.connect(self.prev_media)
        self.prev_button.clicked.connect(self.play_media)
        self.play_button.clicked.connect(self.toggle_play)
        self.next_button.clicked.connect(self.next_media)
        self.next_button.clicked.connect(self.play_media)
        self.speed_plus.clicked.connect(self.increase_speed)
        self.speed_minus.clicked.connect(self.decrease_speed)

        controls.addWidget(self.prev_button)
        controls.addWidget(self.speed_minus)
        controls.addWidget(self.play_button)
        controls.addWidget(self.speed_plus)
        controls.addWidget(self.next_button)
        left_layout.addLayout(controls)

        # Bouton envoi Telegram
        self.send_button = QPushButton("Publier sur Telegram")
        left_layout.addWidget(self.send_button)
        self.send_button.clicked.connect(self.send_to_telegram)

        # Ajouter la zone de log à droite
        main_layout.addLayout(left_layout, stretch=3)
        main_layout.addWidget(self.log_output, stretch=2)
        self.setLayout(main_layout)

    def log(self, message):
        self.log_output.append(message)
        self.log_output.ensureCursorVisible()

    def redirect_print(self):
        builtins.print = lambda *args, **kwargs: self.logger.log(" ".join(map(str, args)))

    def refresh_api_selector(self):
        self.api_selector.clear()
        for key in self.api_keys:
            self.api_selector.addItem(key['name'])
        if self.api_keys:
            self.current_key = self.api_keys[0]

    def select_api_key(self, index):
        if 0 <= index < len(self.api_keys):
            self.current_key = self.api_keys[index]

    def add_api_key(self):
        name, ok = QInputDialog.getText(self, "Nom de la clé", "Nom :")
        if not ok: return
        api_id, ok = QInputDialog.getInt(self, "API ID", "ID :")
        if not ok: return
        api_hash, ok = QInputDialog.getText(self, "API Hash", "Hash :")
        if not ok: return
        self.api_keys.append({"name": name, "api_id": api_id, "api_hash": api_hash})
        save_api_keys(self.api_keys)
        self.refresh_api_selector()

    def select_books_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Sélectionner le dossier des livres")
        if folder:
            self.folder_input.setText(folder)
            self.books = sorted([name for name in os.listdir(folder) if os.path.isdir(os.path.join(folder, name))])
            self.book_selector.clear()
            self.book_selector.addItems(self.books)
            if self.books:
                self.book_selector.setCurrentIndex(0)

    def update_book_preview(self):
        index = self.book_selector.currentIndex()
        if index < 0 or not self.books:
            return
        self.current_book = self.books[index]

        book_path = os.path.join(self.folder_input.text(), self.current_book)
        config_file = os.path.join(book_path, "config", "config.json")
        nom_arabe = ""
        nom_latin = ""
        if os.path.exists(config_file):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    nom_arabe = config.get("nomArabe", "")
                    nom_latin = config.get("nomLatin", "")
            except Exception as e:
                self.logger.log(f"⚠️ Erreur lecture config.json : {e}")

        if nom_arabe:
            self.channel_input.setText(f"{nom_latin if nom_latin else self.current_book} - {nom_arabe} (Majàlisur Rahmàn)")
        else:
            self.channel_input.setText(f"{nom_latin if nom_latin else self.current_book} (Majàlisur Rahmàn)")
        self.current_index = 0
        self.show_current_media()

    def get_image_audio_paths(self):
        book_path = os.path.join(self.folder_input.text(), self.current_book)
        images_dir = os.path.join(book_path, "images")
        audios_dir = os.path.join(book_path, "audios")
        images = sorted([f for f in os.listdir(images_dir) if f.lower().endswith(('.png', '.jpg'))])
        return images, images_dir, audios_dir

    def show_current_media(self):
        images, images_dir, audios_dir = self.get_image_audio_paths()
        if not images:
            self.image_label.setText("Aucune image")
            return

        img_file = os.path.join(images_dir, images[self.current_index])
        pixmap = QPixmap(img_file).scaledToHeight(300, Qt.SmoothTransformation)
        self.image_label.setPixmap(pixmap)

        audio_file = os.path.join(audios_dir, os.path.splitext(images[self.current_index])[0] + ".mp3")
        if os.path.exists(audio_file):
            self.player.setMedia(QMediaContent(QUrl.fromLocalFile(audio_file)))
            self.audio_slider.setValue(0)

    def select_channel_photo(self):
        file_dialog = QFileDialog(self)
        file_dialog.setNameFilter("Images (*.png *.jpg *.jpeg)")
        if file_dialog.exec_():
            selected_files = file_dialog.selectedFiles()
            if selected_files:
                self.channel_photo_path = selected_files[0]
                self.logger.log(f"🖼️ Photo sélectionnée : {self.channel_photo_path}")

    def toggle_play(self):
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_button.setText("▶")
        else:
            self.player.play()
            self.play_button.setText("⏸")

    def play_media(self):
        self.player.play()
        self.play_button.setText("⏸")

    def prev_media(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.show_current_media()

    def next_media(self):
        images, _, _ = self.get_image_audio_paths()
        if self.current_index < len(images) - 1:
            self.current_index += 1
            self.show_current_media()

    def increase_speed(self):
        self.player.setPlaybackRate(self.player.playbackRate() + 0.5)

    def decrease_speed(self):
        self.player.setPlaybackRate(max(0.5, self.player.playbackRate() - 0.5))

    def update_slider(self, position):
        if self.player.duration() > 0:
            self.audio_slider.setValue(int(position * 100 / self.player.duration()))

    def set_position(self, value):
        if self.player.duration() > 0:
            self.player.setPosition(int(value * self.player.duration() / 100))

    def update_duration(self, duration):
        self.audio_slider.setEnabled(duration > 0)

    def send_to_telegram(self):
        if not hasattr(self, "current_key"):
            QMessageBox.warning(self, "Erreur", "Aucune clé API sélectionnée.")
            return

        api_name = self.current_key['name']
        api_id = self.current_key['api_id']
        api_hash = self.current_key['api_hash']

        # Titre du livre (Latin)
        book_title = self.current_book.strip()

        # Définir nom du canal à partir du livre
        book_path = os.path.join(self.folder_input.text(), self.current_book)
        config_file = os.path.join(book_path, "config", "config.json")
        nom_arabe = ""
        nom_latin = ""
        if os.path.exists(config_file):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    nom_arabe = config.get("nomArabe", "")
                    nom_latin = config.get("nomLatin", "")
            except Exception as e:
                self.logger.log(f"⚠️ Erreur lecture config.json : {e}")

        # Titre du canal
        if nom_arabe:
            channel_title = f"{nom_latin if nom_latin else book_title} - {nom_arabe} (Majàlisur Rahmàn)"
        else:
            channel_title = f"{nom_latin if nom_latin else book_title} (Majàlisur Rahmàn)"

        # Vérifier le lien du canal
        raw_input = self.channel_link_input.text().strip()
        channel_link = None
        invite_code = None

        if raw_input:
            # 1) Lien Telegram complet
            pattern_full = r"^https:\/\/t\.me\/([\w\d_]+|\+[\w\d\-_]+)(.*)?$"
            # 2) Nom public (sans https://)
            pattern_username = r"^[A-Za-z0-9_]{5,32}$"
            # 3) Code privé (commence par +) sans https://
            pattern_private = r"^\+[\w\d\-_]+$"

            m = re.match(pattern_full, raw_input)
            if m:
                channel_link = raw_input
                if m.group(1).startswith("+"):
                    invite_code = m.group(1)[1:]  # enlever le "+"
            elif re.match(pattern_private, raw_input):
                channel_link = f"https://t.me/{raw_input}"
                invite_code = raw_input[1:]
            elif re.match(pattern_username, raw_input):
                channel_link = f"https://t.me/{raw_input}"
            else:
                QMessageBox.critical(
                    self,
                    "Lien invalide",
                    "Le lien ou identifiant de canal Telegram est invalide.\n"
                    "Exemples valides :\n"
                    "- https://t.me/majalisur_rahman\n"
                    "- majalisur_rahman\n"
                    "- +7emQpsaabF9mZDdk"
                )
                return
        
        def normalize(text):
            text = unicodedata.normalize("NFD", text)
            return ''.join(c for c in text if unicodedata.category(c) != 'Mn').lower().replace(" ", "_")

        username = normalize(f"majalissurrahman_{book_title}")
        hashtag = self.hashtag_input.text().strip()
        hashtag_nom = ""
        if not hashtag.startswith("#") and hashtag not in ["", "none"]:
            hashtag = f"#{hashtag}"            
        # Normaliser le hashtag pour l'affichage
        if hashtag not in ["", "none"]:
            hashtag_nom = hashtag.lstrip("#").capitalize()

        # ✅ Lancer via QThread
        self.worker = TelegramWorker(self, api_name, api_id, api_hash, channel_title, channel_link, username, hashtag, hashtag_nom)
        self.worker.finished.connect(lambda: self.logger.log("✅ Publication terminée."))
        self.worker.start()


    async def _send_telegram_async(self, api_name, api_id, api_hash, channel_title, channel_link, username, hashtag, hashtag_nom):
        try:
            # Fonction pour normaliser le nom du canal
            def normalize(text):                
                text = unicodedata.normalize("NFD", text)
                text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')  # Remove accents
                text = re.sub(r'\W+', '_', text)  # Replace non-word characters with _
                text = re.sub(r'_+', '_', text)   # Collapse multiple underscores
                return text.strip('_').lower()

            # Chemin de session
            session_path = os.path.join(Path.home(), ".telegram_sessions", f"session_{api_name}.session")
            os.makedirs(os.path.dirname(session_path), exist_ok=True)

            client = TelegramClient(session_path, api_id, api_hash)

            await client.connect()

            if not await client.is_user_authorized():
                phone, ok = QInputDialog.getText(self, "Connexion Telegram", "📱 Entrez votre numéro de téléphone :")
                if not ok or not phone:
                    self.logger.log("❌ Téléphone non fourni.")
                    return

                try:
                    await client.send_code_request(phone)
                except Exception as e:
                    self.logger.log(f"❌ Erreur envoi code : {e}")
                    return

                code, ok = QInputDialog.getText(self, "Connexion Telegram", "🔐 Entrez le code reçu :")
                if not ok or not code:
                    self.logger.log("❌ Code non fourni.")
                    return

                try:
                    await client.sign_in(phone=phone, code=code)
                except Exception as e:
                    self.logger.log(f"❌ Erreur de connexion : {e}")
                    return

            book_path = os.path.join(self.folder_input.text(), self.current_book)
            config_file = os.path.join(book_path, "config", "config.json")
            nom_arabe = ""
            nom_latin = ""
            if os.path.exists(config_file):
                try:
                    with open(config_file, "r", encoding="utf-8") as f:
                        config = json.load(f)
                        nom_arabe = config.get("nomArabe", "")
                        nom_latin = config.get("nomLatin", "")
                except Exception as e:
                    self.logger.log(f"⚠️ Erreur lecture config.json : {e}")

            # === Récupérer ou créer le canal à partir de channel_link
            entity = await get_channel_entity(client, channel_link, self.logger)

            if channel_link not in [None, ""] and entity is None:
                # Si le lien est fourni mais l'entité n'est pas trouvée
                self.logger.log("❌ Lien de canal invalide ou inaccessible.")
                return

            # === Créer le canal si nécessaire
            try:
                entity = await client.get_entity(channel_title)
                self.logger.log(f"✅ Canal trouvé : {entity.title}")
            except (ChannelInvalidError, ValueError):
                self.logger.log("📢 Création du canal en cours...")
                username = normalize(f"mr_{self.current_book}")

                about = f"{channel_title} - {nom_arabe}" if nom_arabe else channel_title

                try:
                    result = await client(CreateChannelRequest(
                        title=channel_title,
                        about=about,
                        megagroup=False
                    ))

                    entity = result.chats[0]
                    self.logger.log(f"✅ Canal créé : {entity.title}")

                    # Ajouter une photo au canal si elle a été sélectionnée
                    try:
                        if self.channel_photo_path and os.path.exists(self.channel_photo_path):
                            await client(EditPhotoRequest(
                                channel=entity,
                                photo=await client.upload_file(self.channel_photo_path)
                            ))
                            self.logger.log("🖼️ Photo du canal définie avec succès.")
                        else:
                            self.logger.log("ℹ️ Aucune photo de canal sélectionnée.")
                    except Exception as e:
                        self.logger.log(f"⚠️ Erreur ajout photo : {e}")


                    # Tenter de définir le username public
                    # try:
                    #     await client(UpdateUsernameRequest(
                    #         channel=entity,
                    #         username=username
                    #     ))
                    #     self.logger.log(f"🔗 Lien du canal : https://t.me/{username}")
                    # except UsernameOccupiedError:
                    #     self.logger.log("⚠️ Ce nom d'utilisateur est déjà pris.")
                    # except Exception as e:
                    #     self.logger.log(f"⚠️ Impossible de définir le lien public : {e}")
                except Exception as e:
                    self.logger.log(f"❌ Erreur création canal : {e}")
                    return

            try:
                full = await client(GetFullChannelRequest(entity))
                real_username = full.chats[0].username
                if real_username:
                    channel_link_prefix = f"https://t.me/{real_username}"
                    self.logger.log(f"🔗 Lien du canal confirmé : {channel_link_prefix}")
                else:
                    # Canal sans nom d'utilisateur → lien privé
                    channel_link_prefix = f"https://t.me/c/{entity.id}"
                    self.logger.log(f"🔐 Canal sans username public. Lien interne : {channel_link_prefix}")
            except Exception as e:
                self.logger.log(f"⚠️ Erreur récupération lien du canal : {e}")
                channel_link_prefix = "https://t.me"

            # === Publier les leçons
            images_dir = os.path.join(book_path, "images")
            audios_dir = os.path.join(book_path, "audios")
            images = sorted([f for f in os.listdir(images_dir) if f.lower().endswith(('.jpg', '.png'))])

            menu_links = []  # pour stocker les liens cliquables

            for i, img_name in enumerate(images, 1):
                base_name = os.path.splitext(img_name)[0]
                self.logger.log(f"🖼️ Publication de {base_name} ({i}/{len(images)})...")

                img_path = os.path.join(images_dir, img_name)
                audio_path = os.path.join(audios_dir, base_name + ".mp3")
                caption = f"{hashtag} {base_name}"

                try:
                    msg = await client.send_file(entity, img_path, caption=caption)
                    if os.path.exists(audio_path):
                        await client.send_file(entity, audio_path)
                        self.logger.log("   ✅ Image + audio envoyés.")
                    else:
                        self.logger.log("   ✅ Image envoyée. (pas d'audio)")

                    # 📌 Construire le lien vers le message
                    msg_id = msg.id
                    msg_url = f"{channel_link_prefix}/{msg_id}"

                    # 🧠 Extraire numéro depuis le nom
                    base_name = os.path.splitext(img_name)[0]
                    dars_num = extraire_numero(base_name)

                    lesson_title = f"{hashtag_nom} {dars_num}"
                    menu_links.append(f"👉 <a href='{msg_url}'>{lesson_title}</a>")
                except Exception as e:
                    self.logger.log(f"❌ Erreur envoi {img_name} : {e}")

            self.logger.log("✅ Tous les médias ont été publiés.")

            # === Publier le menu
            if menu_links:
                self.logger.log("🗂️ Publication du menu des leçons...")

                menu_intro = f"<b>📘 Menu des {hashtag_nom} (Leçons)</b>"

                try:
                    last_msg = None
                    # Diviser en blocs de 99
                    for i in range(0, len(menu_links), 99):
                        block = menu_links[i:i+99]
                        menu_text = menu_intro + "\n\n" + "\n".join(block)
                        last_msg = await client.send_message(entity, menu_text, parse_mode="html", link_preview=False)
                        self.logger.log(f"📄 Menu publié ({i + 1} à {min(i + 99, len(menu_links))})")

                    # === Ajouter le lien "Retour au Programme Principal"
                    main_channel_id = self.main_channel_input.text().strip()
                    if main_channel_id:
                        try:
                            self.logger.log("↩️ Ajout du bouton retour vers le programme principal...")
                            main_entity = await client.get_entity(main_channel_id)

                            # Récupérer le lien public ou interne du canal principal
                            try:
                                full_main = await client(GetFullChannelRequest(main_entity))
                                if full_main.chats[0].username:
                                    main_channel_link = f"https://t.me/{full_main.chats[0].username}"
                                else:
                                    main_channel_link = f"https://t.me/c/{main_entity.id}"
                            except Exception:
                                main_channel_link = main_channel_id  # fallback

                            retour_msg = f"<b><a href='{main_channel_link}'>⬅ Retour au Programme Principal</a></b>"
                            await client.send_message(entity, retour_msg, parse_mode="html", link_preview=False)
                            self.logger.log("✅ Lien retour ajouté.")
                        except Exception as e:
                            self.logger.log(f"⚠️ Impossible d’ajouter le bouton retour : {e}")
                except Exception as e:
                    self.logger.log(f"❌ Erreur envoi menu : {e}")


            # === Ajouter ce canal au Menu global du canal principal (nouveau message, sans vérif)
            try:
                main_channel_id = self.main_channel_input.text().strip()
                if not main_channel_id:
                    self.logger.log("ℹ️ Aucun canal de menu principal spécifié.")
                else:
                    self.logger.log(f"🧭 Connexion au canal de menu : {main_channel_id}")
                    main_entity = await client.get_entity(main_channel_id)

                    nom_affiche = f"{nom_latin} - {nom_arabe}" if nom_arabe else channel_title
                    lien_canal = f"{channel_link_prefix}"

                    # Crée un message HTML simple
                    menu_message = f"◈ <a href='{lien_canal}'>{nom_affiche}</a>"

                    await client.send_message(main_entity, menu_message, parse_mode="html")
                    self.logger.log("📌 Nouveau lien ajouté dans le canal de menu principal.")
            except Exception as e:
                self.logger.log(f"⚠️ Erreur ajout lien dans canal principal : {e}")


        except Exception as e:
            self.logger.log(f"❌ Erreur inattendue (envoi Telegram) : {e}")
            import traceback
            self.logger.log(traceback.format_exc())

        finally:
            await client.disconnect()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = BookUploader()
    win.show()
    sys.exit(app.exec_())
