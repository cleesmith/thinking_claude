import os
import json

# in dev include icecream for easier/faster debug with code name(.py) & line numbers:
# from icecream import ic
# ic.configureOutput(includeContext=True, contextAbsPath=True)

class UserSession:

	def __init__(self):
		#                   year.mo.ver
		self.app_version = "2025.03.15.0"
		self.path_to_settings = None # important
		self.session_id = None
		self.session_cookie = None

		self.provider = "Anthropic"
		self.model = "claude-3-7-sonnet-20250219"

		self.ui_page = None
		self.saved_chat_histories = []
		self.chat_history = ""
		self.send_button = None
		self.thinking_label = None
		self.abort_stream = None
		self.abort = False
		self.chunks = ""
		self.start_time = None
		self.message_container = None
		self.response_message = None

