import os
import json

from dotenv import load_dotenv

# in dev include icecream for easier/faster debug with code name(.py) & line numbers:
# from icecream import ic
# ic.configureOutput(includeContext=True, contextAbsPath=True)

class UserSettings:

	def __init__(self):
		#                   year.mo.ver
		self.app_version = "2025.03.15.0"
		self.signin_time = 0
		self.users_google_sub = None # important
		self.path_to_settings = None # important
		self.google_picture = None
		self.google_given_name = None
		self.google_family_name = None
		self.google_email = None
		self.google_email_verified = None
		self.session_id = None
		self.session_cookie = None
		self.websocket_name = None

		self.settings_updated = False
		self.settings_response = None # includes: redirect url and settings cookies
		self.darkness = None
		self.current_primary_color = '#4CAF50' # green-ish
		self._default_providers = [
			"Anthropic"
		]
		self.provider_models = {}
		self.provider_settings = {
			"Anthropic": {"api_key": "", "max_tokens": 4096, "timeout": 30},
		}

	def get_provider_setting(self, provider_name, key):
		if provider_name in self.provider_settings:
			return self.provider_settings[provider_name][key]
		return ""

	def set_provider_setting(self, provider_name, key, value, response):
		self.provider_settings[provider_name][key] = value
		# self.encrypt_settings('PROVIDER_SETTINGS', response)

	@property
	def providers(self):
		# don't allow providers to be changed
		return self._default_providers.copy()

