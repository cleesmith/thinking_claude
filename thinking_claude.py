import os
import platform
import sys
import signal
import traceback
import uuid
import html
import re
from datetime import datetime
import time

import asyncio
import json

import httpx

from fastapi.responses import JSONResponse, RedirectResponse
from fastapi import HTTPException, Request, Depends, status, WebSocket, WebSocketDisconnect
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

from nicegui import app, ui, run, Client, events

import anthropic

from user_session_settings import UserSession
from user_session_settings import UserSettings

# from icecream import ic
# ic.configureOutput(includeContext=True, contextAbsPath=True)


@ui.page('/', response_timeout=999)
async def home(request: Request, client: Client):
	user_session = UserSession()
	user_settings = UserSettings()

	ui.add_body_html('''
	<script>
		function copyToClipboard(meId, aiId) {
			const meElement = document.getElementById(`c${meId}`);
			const aiElement = document.getElementById(`c${aiId}`);
			let text = "";
			if (meElement) {
				text += meElement.innerText;
			}
			if (aiElement) {
				text += aiElement.innerText;
			}

			// to avoid having to edit (find/replace), remove most Markdown in AI responses:
			let clipText = "";
			const lines = text.split("\\n");
			const filteredLines = lines
				.map(line => {
					const cleanedLine = line.replace(/(\*\*|##|###)/g, "").trim();
					return cleanedLine === "AI:" ? "\\n" + cleanedLine : cleanedLine;
				})
				.filter(line => line !== "content_paste");
			const textWithoutMarkdownAndContentPaste = filteredLines.join("\\n") + "\\n";
			clipText = textWithoutMarkdownAndContentPaste;

			navigator.clipboard.writeText(clipText)
				.then(() => console.log("chat copied to clipboard"))
				.catch(err => console.error("failed to copy chat: ", err));
		}
	</script>
	''')


	def remove_markdown(content: str) -> str:
		content = re.sub(r'\*\*(.*?)\*\*', r'\1', content)  # Bold: **text**
		content = re.sub(r'__(.*?)__', r'\1', content)      # Bold: __text__
		content = re.sub(r'\*(.*?)\*', r'\1', content)      # Italics: *text*
		content = re.sub(r'_(.*?)_', r'\1', content)        # Italics: _text_
		content = re.sub(r'`(.*?)`', r'\1', content)        # Inline code: `text`
		content = re.sub(r'#+\s(.*)', r'\1', content)       # Headings: # text
		content = re.sub(r'!\[.*?\]\(.*?\)', '', content)   # Images: ![alt text](url)
		content = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', content)  # Links: [text](url)
		content = re.sub(r'^\s*[-\*]\s+', '', content, flags=re.MULTILINE)  # Bulleted lists: - text or * text
		return content

	async def update_response_message_container(content: str):
		# show elapsed time, clock time, calendar date, and update stamp in ui.chat_message:
		end_time = time.time()
		elapsed_time = end_time - user_session.start_time
		minutes, seconds = divmod(elapsed_time, 60)
		end_time_date = f"{int(minutes)}:{seconds:.2f} elapsed at " + datetime.now().strftime("%I:%M:%S %p") + " on " + datetime.now().strftime("%A, %b %d, %Y")
		if content.startswith('<<FIN_LOCAL>>'):
			try:
				user_session.thinking_label.set_visibility(False)
				user_session.send_button.set_enabled(True)
				user_session.abort_stream.set_visibility(False)
			except Exception as e:
				print(e)
				pass
			return
		try:
			clean_content = remove_markdown(content)
			escaped_content = html.escape(clean_content)
			with user_session.message_container:
				await ui.context.client.connected() # necessary?
				user_session.response_message.clear() # cleared for each chunk = why?
				with user_session.response_message:
					user_session.chunks += "" if escaped_content is None else escaped_content
					ui.html(f"<pre style='white-space: pre-wrap;'><br>AI:\n{user_session.chunks}</pre>")
				user_session.response_message.props(f'stamp="{end_time_date}"')
				user_session.response_message.update()
				await ui.run_javascript('scrollable.scrollTo(0, scrollable.scrollHeight)')
		except Exception as e:
			print(e)
			user_session.thinking_label.set_visibility(False)
			user_session.send_button.set_enabled(True)
			user_session.abort_stream.set_visibility(False)
			pass


	def set_abort(value):
		user_session.abort = value

	def convert_to_int(s: str, default: int) -> int:
		try:
			return int(s)
		except ValueError:
			return default
		except Exception:
			return default


	async def AnthropicResponseStreamer(prompt):
		# if user_session.model is None: yield ""; return # sometimes model list is empty
		# pm = "Anthropic"
		# provider_timeout = user_settings.get_provider_setting(pm, 'timeout')

		# # Prepare the prompt with chat history and current request
		# prompt = ""
		
		# # prefix the no-markdown instruction directly to the user prompt if needed
		# if args.no_markdown:
		#     prompt = "Never respond with Markdown formatting, plain text only.\n\n"
			
		# if current_chat_history:
		#     prompt += current_chat_history
		#     if not prompt.endswith("\n\n"):
		#         prompt += "\n\n"
		
		# Add the current input to the prompt
		prompt += f"ME: {prompt}"

		# Calculate a safe max_tokens value
		estimated_input_tokens = int(len(prompt) // 4)  # conservative estimate
		total_estimated_input_tokens = estimated_input_tokens

		max_safe_tokens = max(5000, 204648 - total_estimated_input_tokens - 2000)  # 2000 token buffer for safety
		# Use the minimum of the requested max_tokens and what we calculated as safe:
		max_tokens = int(min(12000, max_safe_tokens))

		# Ensure max_tokens is always greater than thinking budget
		if max_tokens <= 32000:
			max_tokens = 32000 + 12000

		# Prepare messages list - simple user message with prefixed instruction
		messages = [{"role": "user", "content": prompt}]

		full_response = ""
		thinking_content = ""

		start_time = time.time()

		dt = datetime.fromtimestamp(start_time)
		formatted_time = dt.strftime("%A %B %d, %Y %I:%M:%S %p").replace(" 0", " ").lower()
		yield(f"****************************************************************************")
		yield(f"*  sending to API at: {formatted_time}")
		yield(f"*  ... standby, as this usually takes a few minutes")
		yield(f"*  ... press CTRL+C at any time to interrupt and exit")
		yield(f"****************************************************************************")

		try:
			# client = anthropic.Anthropic(
			#   # api_key= # see: ~/.zshrc or os environ export's or ~/.config/
			#   timeout=provider_timeout,
			#   max_retries=0,
			# )
			# params = {
			#   "messages": [{"role": "user", "content": prompt}],
			#   "model": user_session.model,
			#   "max_tokens": user_settings.get_provider_setting('Anthropic', 'max_tokens'),
			#   }
			# with client.messages.stream(**params) as stream:
			#   for content in stream.text_stream:
			#       if user_session.abort:
			#           set_abort(False)
			#           yield f"\n... response stopped by button click."
			#           stream.close()  # properly close the generator
			#           break  # exit the generator cleanly
			#       if isinstance(content, str):
			#           cleaned_content = content.replace("**", "") # no ugly Markdown in plain text
			#           yield cleaned_content
			#       else:
			#           yield "" # handle None or any unexpected type by yielding an empty string

			client = anthropic.Anthropic(
			    timeout=300,
			    max_retries=0  # default is 2
			)

			messages = [{"role": "user", "content": prompt}]

			with client.beta.messages.stream(
				model="claude-3-7-sonnet-20250219",
				max_tokens=max_tokens,
				messages=messages,
				thinking={
					"type": "enabled",
					"budget_tokens": 32000
				},
				betas=["output-128k-2025-02-19"]
			) as stream:
				# track both thinking and text output
				for event in stream:
					# # check if an interrupt was requested
					# if exit_requested:
					#     print("\nInterrupting API request as requested...")
					#     break

					if event.type == "content_block_delta":
						if event.delta.type == "thinking_delta":
							thinking_content += event.delta.thinking
							cleaned_content = event.delta.thinking.replace("**", "") # no ugly Markdown in plain text
							yield cleaned_content
						elif event.delta.type == "text_delta":
							full_response += event.delta.text
							# Display the response in real-time
							# print(event.delta.text, end='', flush=True)
							cleaned_content = event.delta.text.replace("**", "") # no ugly Markdown in plain text
							yield cleaned_content

		except Exception as e:
			print(e)
			yield f"Error:\nAnthropic's response for model: {user_session.model}\n{e}"


	# map the provider to the corresponding streamer, so
	# the 'async def' for it must be defined before this point.
	STREAMER_MAP = {
		"Anthropic": AnthropicResponseStreamer,
	}

	async def run_streamer(provider, prompt):
		streamer_function = STREAMER_MAP.get(provider)
		if streamer_function is None:
			yield f"No streamer found for provider: {provider}"
			return
		async for chunk in streamer_function(prompt):
			if user_session.abort:
				set_abort(False)
				await asyncio.sleep(1.0)
				return  # exit the generator cleanly?
			yield chunk


	# **********************
	# start ui using NiceGUI:
	# **********************

	user_settings.current_primary_color = '#4CAF50' # green
	ui.colors(primary=str(user_settings.current_primary_color))

	darkness = ui.dark_mode()
	darkness_value = user_settings.darkness if user_settings.darkness is not None else True
	darkness.set_value(darkness_value)

	user_session.provider = 'Anthropic'
	user_session.model = 'claude-3-7-sonnet-20250219'

	async def windowInnerHeight():
		window_innerHeight = await ui.run_javascript(f"window.innerHeight")
		return window_innerHeight

	# async def windowInnerWidth():
	#   window_innerWidth = await ui.run_javascript(f"window.innerWidth")
	#   return window_innerWidth

	async def copy_chat():
		text = await ui.run_javascript(f"getElement({user_session.message_container.id}).innerText")
		# this also works and may make more sense than using: user_settings.message_container.id:
		# text = await ui.run_javascript(f"return document.getElementById('scrollable').innerText;")

		# let's use a single generator expression to iterate through the lines of text, 
		# to filter by removing Markdown: "**", "##", "###", stripping whitespace, 
		# and ignoring lines with just "content_paste" (i.e. copy button)
		# ... which should be more efficient for large chat texts 
		# because it avoids unnecessary list creation and 
		# performs both filtering operations in a single pass:
		filtered_lines = (
			re.sub(r"\*\*|##|###", "", line).strip() 
			for line in text.splitlines() 
			if re.sub(r"\*\*|##|###", "", line).strip() != "content_paste"
			if re.sub(r"\*\*|##|###", "", line).strip() != "edit"
		)
		text_without_markdown_and_content_paste = "\n".join(filtered_lines)
		escaped_text = text_without_markdown_and_content_paste
		if escaped_text.startswith('"') and escaped_text.endswith('"'):
			safe_text = escaped_text[1:-1]
		else:
			safe_text = escaped_text
		safe_text_js = json.dumps(safe_text)
		return safe_text_js


	async def clear_chat():
		user_session.message_container.clear()
		user_session.chat_history = ""


	async def save_chat(text) -> None:
		text = await ui.run_javascript(f"getElement({user_session.message_container.id}).innerText")
		# generate the filename with epoch timestamp
		timestamp = int(time.time())
		filename = f"{timestamp}_Keys2Text_chat_history.txt"
		try:
			# filter out markdown and specific unwanted content
			filtered_lines = (
				re.sub(r"\*\*|##|###", "", line).strip()
				for line in text.splitlines()
				if re.sub(r"\*\*|##|###", "", line).strip() != "content_paste"
				if re.sub(r"\*\*|##|###", "", line).strip() != "edit"
			)
			text_without_markdown_and_content_paste = "\n".join(filtered_lines)
			if len(text_without_markdown_and_content_paste) > 0:
				# when running app online, we download:
				ui.download(text_without_markdown_and_content_paste.encode('utf-8'), filename)

				# when running app locally, we may save to file:
				# filepath = os.path.join(Keys2Text_APP_PATH, filename)
				# with open(filepath, "w", encoding='utf-8') as file:
				#   file.write(text_without_markdown_and_content_paste)
				# user_settings.saved_chat_histories.append(filepath)

				ui.notify(f"Chat history will be downloaded as: {filename}", position="top")
			else:
				ui.notify(f"There is no chat history to download ?", position="top")
		except Exception as e:
			ui.notify(f"Error downloading chat history file: {filename}:\n{e}", position="top")

	def escape_js_string(text):
		"""escape a Python string for safe inclusion in JavaScript code."""
		return json.dumps(text)  # automatically escapes quotes, backslashes, newlines, etc.


	async def send_prompt_to_ai() -> None:
		prompt_sent = bool(user_prompt.value and user_prompt.value.strip())
		user_session.provider = user_session.provider
		user_session.model = user_session.model
		if not prompt_sent:
			ui.notify("Prompt is empty, please type something.", position="top", timeout=2000)
			return
		else:
			user_session.chat_history += user_prompt.value + " \n"
		question = user_prompt.value
		user_prompt.value = ''

		# disable Send button, start Thinking, show Abort stream:
		user_session.send_button.set_enabled(False)
		user_session.thinking_label.set_visibility(True)
		user_session.abort_stream.set_visibility(True)

		# track the elapsed time from sent message/prompt until end of response message:
		user_session.start_time = time.time()

		user_session.response_message = None
		with user_session.message_container:
			timestamp = datetime.now().strftime("%I:%M:%S %p") + " on " + datetime.now().strftime("%A, %b %d, %Y")
			me_message = ui.chat_message(sent=True, stamp=timestamp)
			me_message.clear()
			with me_message:
				ui.html(f"<pre style='white-space: pre-wrap;'>ME:   {user_session.provider} - {user_session.model}\n{question}</pre>")

			user_session.response_message = ui.chat_message(sent=False, stamp=timestamp)

		# accumulate/concatenate AI's response as chunks:
		user_session.chunks = ""

		try:
			await ui.context.client.connected() # necessary?
			await ui.run_javascript('scrollable.scrollTo(0, scrollable.scrollHeight)')
		except Exception as e:
			pass # no biggie, if can't scroll then user can do it manually

		async for chunk in run_streamer(user_session.provider, user_session.chat_history):
			if user_session.abort:
				set_abort(False)
				break
			await ui.context.client.connected() # necessary?
			user_session.response_message.clear() # cleared for each chunk = why?
			with user_session.response_message:
				user_session.chunks += "" if chunk is None else chunk
				ui.html(f"<pre style='white-space: pre-wrap;'><br>AI:\n{user_session.chunks}</pre>")
			
			# show elapsed time, clock time, calendar date, and update stamp in ui.chat_message:
			end_time = time.time()
			elapsed_time = end_time - user_session.start_time
			minutes, seconds = divmod(elapsed_time, 60)
			end_time_date = f"{int(minutes)}:{seconds:.2f} elapsed at " + datetime.now().strftime("%I:%M:%S %p") + " on " + datetime.now().strftime("%A, %b %d, %Y")
			user_session.response_message.props(f'stamp="{end_time_date}"')
			user_session.response_message.update()
			try:
				await ui.context.client.connected() # necessary?
				await ui.run_javascript('scrollable.scrollTo(0, scrollable.scrollHeight)')
			except Exception as e:
				user_session.thinking_label.set_visibility(False)
				user_session.send_button.set_enabled(True)

			user_session.thinking_label.set_visibility(False)
			user_session.send_button.set_enabled(True)
			user_session.abort_stream.set_visibility(False)

		user_session.chunks = ""
		# ************************
		# *** stream has ended ***
		# ************************

		async def copy_prompt_ME_back_to_prompt(prompt):
			try:
				prompt_text = await ui.run_javascript(f"return document.getElementById('c{prompt.id}').innerText;")
				lines = prompt_text.splitlines()
				if len(lines) > 2:
					prompt_text = '\n'.join(lines[1:-1])
				else:
					prompt_text = '\n'.join(lines)
				user_prompt.value = prompt_text
				user_prompt.update()
				# annoying = ui.notify(f"The prompt inside ME: message was copied back to Prompt, so now you can edit and re-Send.", position="top", timeout=2000)
			except Exception as e:
				ui.notify(f"Unable to copy ME: message back to Prompt.  Error: {e}", position="top")

		# setup/handle copying a ME: + AI: chat pair to clipboard 
		# via nicegui button and javascript copyToClipboard in: 
		#   ui.add_body_html('''<script>... :
		try:
			with user_session.message_container:
				# why is the above 'with' needed? 
				#   to access user_session.response_message properly 
				#   in order to get the text inside of 2 div's (me/ai),
				#   i.e. the 'div id=cNUM' will be found.

				with user_session.response_message:
					chat_ai_response = user_session.response_message

				async def show_checkmark() -> None:
					me_ai_copy_button.props('icon=check color=green')
					await asyncio.sleep(2.0)
					me_ai_copy_button.props('icon=content_paste color=primary')

				me_ai_copy_button = ui.button(icon='content_copy', on_click=show_checkmark) \
					.on('click', js_handler=f'() => copyToClipboard("{me_message.id}", "{chat_ai_response.id}")') \
					.tooltip("copy this section (ME: + AI:) to the clipboard") \
					.props('icon=content_copy round flat') \
					.style("padding: 1px 1px; font-size: 9px;")

				ui.button(
					icon='edit',
					on_click=lambda: copy_prompt_ME_back_to_prompt(me_message)
				) \
				.tooltip("reuse this prompt for editing") \
				.props('icon=edit round flat') \
				.style("padding: 1px 1px; font-size: 9px;")

				ui.html(f"<pre style='white-space: pre-wrap;'><br></pre>")
			await ui.run_javascript('scrollable.scrollTo(0, scrollable.scrollHeight)')
		except Exception as e:
			ui.notify(f"Error: {e}", position="top")


	# *************************
	# page layout and elements:
	# *************************

	# custom css to remove the one pointy corner (that's used with avatar's) 
	# and ensure all corners are rounded:
	ui.add_head_html('''
	<style>
		/* remove the pointy pseudo-element */
		.q-message-text::before {
			content: none !important;
			display: none !important;
		}

		/* ensure all corners of the message bubble are rounded */
		.q-message-text {
			border-radius: 12px !important;
			background-color: transparent !important;
			border: 1px solid #B0B0B0 !important; /* add a neutral grey border */
		}

		.q-message-text-content {
			border-radius: 12px !important;
			background-color: transparent !important;
		}

		.dark .q-message-text-content {
			color: #cccccc !important; /* text color for dark mode */
		}
	</style>
	''')

	# this is not easy to understand, most of the issues are bad js syntax (not properly escaped)
	#   using ui.run_javascript is only possible after a client is connected
	# this doesn't always help, so we see "TimeoutError: JavaScript did not respond within 1.0 s":
	await ui.context.client.connected()

	wih = await windowInnerHeight() - 300 # leave some empty space at bottom of page
	# wiw = await windowInnerWidth() - 200 # not needed, width is responsive

	with ui.header().classes('bg-transparent text-gray-800 dark:text-gray-200 z-10 mt-0').style('margin-top: 0; padding-top: 0;'):

		with ui.row().classes("w-full mb-0 mt-0 ml-0"):

			with ui.column().classes("flex-none p-0"):
				# SEND button and thinking label
				with ui.row().classes("items-center"):
					user_session.send_button = (
						ui.button(
							icon="send",
							on_click=lambda: send_prompt_to_ai(),
						)
						.props('outline')
						.classes("ml-0 mr-2 text-sm px-4 py-2 mt-5")
						.style(
							"box-shadow: 0 0 1rem 0 #546e7a; transition: transform 0.3s ease;"
						)
						# .props('icon=img:https://www.slipthetrap.com/images/Aidetour.png')
					)

					with user_session.send_button:
						with ui.tooltip('').props("flat fab"):
							ui.html("send Prompt")

					user_session.thinking_label = ui.spinner('grid', size='sm').classes("ml-1 mt-5") # , color='green')

			user_session.thinking_label.set_visibility(False)
			user_session.send_button.set_enabled(False)
			user_session.send_button.set_enabled(True)


			with ui.column().classes("flex-grow p-0"):
				user_prompt = (
					ui.textarea(label="Prompt:", placeholder=f"Enter your prompt here...")
					.classes("w-full")
					.props("clearable")
					.props("rows=5")
					.props("spellcheck=false")
					.props("autocomplete=off")
					.props("autocorrect=off")
					.props("tabindex=0")
				)


		with ui.row().classes("w-full mb-0.1 space-x-0"):
			# https://fonts.google.com/icons?icon.query=clip&icon.size=24&icon.color=%23e8eaed

			async def scroll_to_bottom():
				await ui.run_javascript('scrollable.scrollTo(0, scrollable.scrollHeight)')

			ui.button(icon='arrow_downward', on_click=scroll_to_bottom) \
			.tooltip("scroll to bottom") \
			.props('no-caps flat fab-mini')

			async def scroll_to_top():
				await ui.run_javascript('scrollable.scrollTo(0, 0)')

			ui.button(icon='arrow_upward', on_click=scroll_to_top) \
			.tooltip("scroll to top") \
			.props('no-caps flat fab-mini')

			async def copy_checkmark() -> None:
				copy_all_button.props('icon=check color=green')
				await asyncio.sleep(3.0)
				copy_all_button.props('icon=content_paste color=primary')

			safe_text_js_handler = """
			() => {
				let text = document.getElementById('scrollable').innerText;
				let filteredLines = Array.from(text.split('\\n'))
					.map(line => line.replace(/\\*\\*|##|###/g, "").trim())
					.filter(line => line !== "content_paste" && line !== "edit" && line !== "content_copy");
				let textWithoutMarkdownAndContentPaste = filteredLines.join('\\n');
				let escapedText = textWithoutMarkdownAndContentPaste;
				let safeText;
				if (escapedText.startsWith('"') && escapedText.endsWith('"')) {
					safeText = escapedText.slice(1, -1);
				} else {
					safeText = escapedText;
				}
				try {
					navigator.clipboard.writeText(safeText).then(() => {
						console.log('Chat history copied successfully');
					}).catch(err => {
						console.error('Failed to copy text:', err);
						alert('Clipboard operation failed. Please copy manually.');
					});
				} catch (err) {
					console.error('Clipboard API not supported:', err);
					alert('Clipboard operation failed. Please copy manually.');
				}
			}
			"""

			copy_all_button = ui.button(icon='content_copy', on_click=copy_checkmark) \
				.on('click', js_handler=safe_text_js_handler) \
				.tooltip("copy entire chat history to clipboard") \
				.props('no-caps flat fab-mini')

			ui.button(icon="delete_sweep", on_click=clear_chat) \
			.tooltip("clear chat history") \
			.props('no-caps flat fab-mini')

			ui.button(icon='save_as', on_click=save_chat) \
			.props('no-caps flat fab-mini') \
			.tooltip("download entire chat history as a text file")

			def update_tooltip(button, tooltip_text):
				button.props(f'tooltip="{tooltip_text}"')

			with ui.element():
				dark_button = ui.button(icon='dark_mode', on_click=lambda: [darkness.set_value(True), update_tooltip(dark_button, 'be Dark')]) \
					.props('flat fab-mini').tooltip('be Dark').bind_visibility_from(darkness, 'value', value=False)
				light_button = ui.button(icon='light_mode', on_click=lambda: [darkness.set_value(False), update_tooltip(light_button, 'be Light')]) \
					.props('flat fab-mini').tooltip('be Light').bind_visibility_from(darkness, 'value', value=True)

			chat_settings = ui.button(icon='settings', on_click=lambda: chat_settings_dialog()) \
			.tooltip("Chat    Settings") \
			.props('no-caps flat fab-mini')

			def reload_app(user_session, user_settings):
				# ui.run_javascript("setTimeout(function() { window.location.replace(window.location.href); }, 1000);")
				ui.run_javascript("location.reload(true);")
				# ui.run_javascript("""
				# if (navigator.userAgent.includes('Safari') && !navigator.userAgent.includes('Chrome')) {
				#     setTimeout(function() { location.reload(true); }, 1000);
				# } else {
				#     setTimeout(function() { window.location.replace(window.location.href); }, 1000);
				# }
				# """)

			ui.button(icon='restart_alt', on_click=lambda: reload_app(user_session, user_settings)) \
			.tooltip("Reload app") \
			.props('no-caps flat fab-mini')

			ui.button(
				icon='logout',
				on_click=lambda: app.shutdown()
			) \
			.tooltip("Shutdown app") \
			.props('no-caps flat fab-mini')


			# this only appears during long streaming responses:
			user_session.abort_stream = ui.button(
				icon='content_cut',
				on_click=lambda e: set_abort(True)
			) \
			.tooltip("stop AI's response") \
			.props('flat outline round color=red').classes('shadow-lg')

			user_session.abort_stream.set_visibility(False)


	#########################################################################
	# this app's heart and raison d'etre ...
	# which contains the text generated responses from the AI model:
	#########################################################################
	with ui.element('div').classes('flex flex-col min-h-full w-full mx-auto'):
		user_session.message_container = (
			ui.element("div")
			.classes(
				"w-full overflow-auto p-2 rounded flex-grow"
			)
			.props('id="scrollable"')
			.style(f'height: {wih}px; font-size: 15px !important; white-space: pre-wrap;') 
		)
		ui.separator().props("size=4px color=primary")  # insinuate bottom of chat history


	async def chat_settings_dialog():
		with ui.dialog() as settings_popup:
			settings_popup.props("persistent")
			settings_popup.props("maximized")
			with settings_popup, ui.card().classes("w-full items-center"):
				with ui.column():
					with ui.row().classes("w-full items-center mb-0.1 space-x-2"):
						ui.space()
						ui.label('ThinkingClaude Settings').style("font-size: 20px; font-weight: bold").classes("m-0")

						def save_settings(dark_mode_switch, primary_color, provider_inputs):
							user_settings.settings_updated = True
							user_settings.darkness = dark_mode_switch.value
							user_settings.current_primary_color = primary_color
							for provider, inputs in provider_inputs.items():
							  for key, key_object in inputs.items():
								  if key == 'timeout':
									  user_settings.set_provider_setting(provider, key, convert_to_int(key_object.value, None), user_settings.settings_response)
								  else:
									  user_settings.set_provider_setting(provider, key, key_object.value, user_settings.settings_response)
							ui.notify('Settings saved, but the changes only apply after reloading the app.', position="top", color='warning')
							chat_settings.props('color=primary')
							chat_settings.tooltip(f"Chat Settings")
							chat_settings.update()  # refresh the UI to reflect changes

							user_settings.save_as_json_file(user_session)

						ui.space()
						ui.button(icon='save_as', on_click=lambda: save_settings(
							dark_mode_switch,
							get_primary_color(),
							provider_inputs
						)).tooltip("Save settings").props('no-caps flat dense')

						async def close_clear_dialog():
							settings_popup.close()
							settings_popup.clear() # removes the hidden settings_popup ui.dialog

						ui.button(
							icon='close', 
							on_click=close_clear_dialog
							) \
							.tooltip("Close") \
							.props('no-caps flat fab-mini')

					with ui.column().classes('mb-2'):
						with ui.row().classes('w-full items-center'):
							dark_light = darkness.value
							ui.label('Light or Dark').classes('ml-2 mr-0')
							dark_mode_switch = ui.switch(value=dark_light).classes('ml-0').tooltip("select light or dark mode")

							def set_primary_color(color):
								user_settings.current_primary_color = color
								js_code = f"""
								document.getElementById('picker').style.setProperty('color', '{color}', 'important');
								"""
								ui.run_javascript(js_code)

							def get_primary_color():
								return user_settings.current_primary_color

							with ui.button(icon='palette').props('id=picker').props('no-caps flat fab-mini').classes('ml-4').tooltip("change color, note: changes only applied after restarting app") as button:
								button = ui.color_picker(on_pick=lambda e: set_primary_color(e.color))
								button.q_color.props('''default-view=palette no-header no-footer :palette="['#6200ea', '#ff0000', '#ff8000', '#d6d600', '#4CAF50', '#00a3a3', '#007bf5', '#7b00f5', '#d600d6', '#333333']"''')

					with ui.row().classes('w-full items-center'):
						ui.label('Provider Settings:').style('font-size: 18px; font-weight: bold; margin-bottom: 8px;')
						ui.space()
						ui.label("* be careful as these settings contain API keys, i.e. money!").style('font-size: 12px; font-style: italic; margin-bottom: 8px; color: #546e7a;')

						provider_inputs = {}
						with ui.column().classes('w-full'):
							for provider_name in user_settings.provider_settings:
								with ui.expansion(provider_name).classes('w-full mb-2'):
									with ui.column().classes('w-full'):
										inputs = {}
										inputs['api_key'] = ui.input(
											label='API Key', 
											password=True, 
											password_toggle_button=True, 
											value=user_settings.get_provider_setting(provider_name, 'api_key')) \
										.classes('w-full mb-2') \
										.props("spellcheck=false") \
										.props("autocomplete=off") \
										.props("autocorrect=off")

										inputs['timeout'] = ui.input(
											label='Timeout', 
											value=user_settings.get_provider_setting(provider_name, 'timeout')) \
										.classes('w-full mb-2') \
										.props("spellcheck=false") \
										.props("autocomplete=off") \
										.props("autocorrect=off")

										if provider_name == 'Anthropic':
											inputs['max_tokens'] = ui.input(
												label='Max Tokens', 
												value=user_settings.get_provider_setting(provider_name, 'max_tokens')) \
											.classes('w-full mb-2') \
											.props("spellcheck=false") \
											.props("autocomplete=off") \
											.props("autocorrect=off")

										provider_inputs[provider_name] = inputs

							ui.separator().props("size=4px color=primary") # insinuate bottom of settings

		settings_popup.open()


def main():
	ui.run(
		title="spud",
		reload=False,
		dark=True,
	)


if __name__ in {"__main__", "__mp_main__"}:
	main()
