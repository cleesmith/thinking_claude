import argparse
import datetime
import os
import sys
import html
import re
from datetime import datetime
import time
import asyncio
import json

import anthropic

from fastapi import Request
from nicegui import app, ui, run, Client, events

args = None
user_session = None

def setup_argument_parser():
	parser = argparse.ArgumentParser(description='Chat with Claude 3.7 Sonnet and 32K tokens of thinking.')
	parser.add_argument('--request_timeout', type=int, default=300, 
						help='Maximum timeout for each streamed chunk of output (default: 300 seconds or about 5 minutes)')
	parser.add_argument('--thinking_budget', type=int, default=32000, 
						help='Maximum tokens for AI thinking (default: 32000)')
	parser.add_argument('--max_tokens', type=int, default=12000, 
						help='Maximum tokens for output (default: 12000)')
	parser.add_argument('--context_window', type=int, default=204648, 
						help='Context window for Claude 3.7 Sonnet (default: 204648)')
	parser.add_argument('--save_dir', type=str, default=".", 
						help='Directory to save output files (default: current directory)')
	parser.add_argument('--chat_history', type=str, default=None, 
						help='Optional chat history text file to continue a conversation')
	parser.add_argument('--no_markdown', action='store_true', 
						help='Tell Claude not to respond with Markdown formatting')
	return parser

def load_chat_history(history_file):
	if not os.path.exists(history_file):
		print(f"Chat history file not found: {history_file}")
		return ""
	try:
		with open(history_file, 'r', encoding='utf-8') as f:
			content = f.read()
		return content
	except Exception as e:
		print(f"Error loading chat history: {e}")
		return ""

class UserSession:
	def __init__(self):
		self.app_version = "2025.03.15.0"
		self.provider = "Anthropic"
		self.model = "claude-3-7-sonnet-20250219"
		self.ui_page = None
		self.chat_history = ""
		self.send_button = None
		self.thinking_label = None
		self.chunks = ""
		self.start_time = None
		self.message_container = None
		self.response_message = None


@ui.page('/', response_timeout=999)
async def home(request: Request, client: Client):
	ui.add_body_html(r'''
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
			const lines = text.split("\n");
			const filteredLines = lines
				.map(line => {
					const cleanedLine = line.replace(/(\*\*|##|###)/g, "").trim();
					return cleanedLine === "AI:" ? "\n" + cleanedLine : cleanedLine;
				})
				.filter(line => line !== "content_paste");
			const textWithoutMarkdownAndContentPaste = filteredLines.join("\n") + "\n";
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

	# async def update_response_message_container(content: str):
	#   # show elapsed time, clock time, calendar date, and update stamp in ui.chat_message:
	#   end_time = time.time()
	#   elapsed_time = end_time - user_session.start_time
	#   minutes, seconds = divmod(elapsed_time, 60)
	#   end_time_date = f"{int(minutes)}:{seconds:.2f} elapsed at " + datetime.now().strftime("%I:%M:%S %p") + " on " + datetime.now().strftime("%A, %b %d, %Y")
	#   if content.startswith('<<FIN_LOCAL>>'):
	#       try:
	#           user_session.thinking_label.set_visibility(False)
	#           user_session.send_button.set_enabled(True)
	#       except Exception as e:
	#           print(e)
	#           pass
	#       return
	#   try:
	#       clean_content = remove_markdown(content)
	#       escaped_content = html.escape(clean_content)
	#       with user_session.message_container:
	#           await ui.context.client.connected() # necessary?
	#           user_session.response_message.clear() # cleared for each chunk = why?
	#           with user_session.response_message:
	#               user_session.chunks += "" if escaped_content is None else escaped_content
	#               ui.html(f"<pre style='white-space: pre-wrap;'><br>AI:\n{user_session.chunks}</pre>")
	#           user_session.response_message.props(f'stamp="{end_time_date}"')
	#           user_session.response_message.update()
	#           await ui.run_javascript('scrollable.scrollTo(0, scrollable.scrollHeight)')
	#   except Exception as e:
	#       print(e)
	#       user_session.thinking_label.set_visibility(False)
	#       user_session.send_button.set_enabled(True)
	#       pass


	async def AnthropicResponseStreamer(prompt):
		global args, user_session

		print(f"prompt:\n{prompt}\n")

		# calculate a safe max_tokens value
		estimated_input_tokens = int(len(prompt) // 4)  # conservative estimate
		total_estimated_input_tokens = estimated_input_tokens

		max_safe_tokens = max(5000, args.context_window - total_estimated_input_tokens - 2000)  # 2000 token buffer for safety
		# use the minimum of the requested max_tokens and what we calculated as safe:
		max_tokens = int(min(args.max_tokens, max_safe_tokens))

		# ensure max_tokens is always greater than thinking budget
		if max_tokens <= args.thinking_budget:
			max_tokens = args.thinking_budget + args.max_tokens

		messages = [{"role": "user", "content": prompt}]

		full_response = ""
		thinking_content = ""

		try:
			client = anthropic.Anthropic(
				timeout=args.request_timeout,
				max_retries=0  # default is 2
			)

			messages = [{"role": "user", "content": prompt}]

			with client.beta.messages.stream(
				model="claude-3-7-sonnet-20250219",
				max_tokens=max_tokens,
				messages=messages,
				thinking={
					"type": "enabled",
					"budget_tokens": args.thinking_budget
				},
				betas=["output-128k-2025-02-19"]
			) as stream:
				in_thinking_mode = False
				# track both thinking and text output
				for event in stream:
					if event.type == "content_block_delta":
						if event.delta.type == "thinking_delta":
							# If this is our first thinking delta, open the thinking tag
							if not in_thinking_mode:
								in_thinking_mode = True
								yield "=== THINKING ===\n"
							thinking_content += event.delta.thinking
							cleaned_content = event.delta.thinking.replace("**", "")  # no ugly Markdown in plain text
							yield cleaned_content
						elif event.delta.type == "text_delta":
							# If we were in thinking mode and now getting text, close the thinking tag
							if in_thinking_mode:
								in_thinking_mode = False
								yield "\n=== END THINKING ===\n\n\n"
							full_response += event.delta.text
							cleaned_content = event.delta.text.replace("**", "")  # no ugly Markdown in plain text
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
			yield chunk


	# **********************
	# start ui using NiceGUI:
	# **********************

	ui.colors(primary=str('#4CAF50'))
	darkness = ui.dark_mode()
	darkness_value = True
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
				ui.download(text_without_markdown_and_content_paste.encode('utf-8'), filename)
				ui.notify(f"Chat history will be downloaded as: {filename}", position="top")
			else:
				ui.notify(f"There is no chat history to download ?", position="top")
		except Exception as e:
			ui.notify(f"Error downloading chat history file: {filename}:\n{e}", position="top")

	def escape_js_string(text):
		"""escape a Python string for safe inclusion in JavaScript code."""
		return json.dumps(text)  # automatically escapes quotes, backslashes, newlines, etc.


	async def send_prompt_to_ai() -> None:
		global previous_chat_history
		prompt_sent = bool(user_prompt.value and user_prompt.value.strip())
		user_session.provider = user_session.provider
		user_session.model = user_session.model
		if not prompt_sent:
			ui.notify("Prompt is empty, please type something.", position="top", timeout=2000)
			return
		else:
			user_session.chat_history += user_prompt.value + " \n"

		prompt = ""

		if previous_chat_history:
			print(f"previous_chat_history:\n{previous_chat_history[:70]}\n")
			prompt += f"\n=== PREVIOUS CHAT HISTORY ===\n{previous_chat_history}\n=== END PREVIOUS CHAT HISTORY ===\n"
			if not prompt.endswith("\n\n"):
				prompt += "\n\n"
			# avoid adding it multiple times:
			previous_chat_history = None

		prompt += user_prompt.value
		
		# prompt += f"ME: {prompt}"
		if args.no_markdown:
			prompt += "\n=== IMPORTANT ===\nNever respond with Markdown formatting, plain text only but simple numbers and hyphens are allowed for lists ... like this: 1. whatever and - whatever.\n=== END IMPORTANT ===\n\n"

		print(f">>> full prompt:\n{prompt}\n<<< end\n")

		# clear/reset for next prompt by user:
		user_prompt.value = ''

		# disable Send button, start Thinking:
		user_session.send_button.set_enabled(False)
		user_session.thinking_label.set_visibility(True)

		# track the elapsed time from sent message/prompt until end of response message:
		user_session.start_time = time.time()

		user_session.response_message = None
		with user_session.message_container:
			timestamp = datetime.now().strftime("%I:%M:%S %p") + " on " + datetime.now().strftime("%A, %b %d, %Y")
			me_message = ui.chat_message(sent=True, stamp=timestamp)
			me_message.clear()
			with me_message:
				ui.html(f"<pre style='white-space: pre-wrap;'>ME:\n{prompt}</pre>")

			user_session.response_message = ui.chat_message(sent=False, stamp=timestamp)

		# accumulate/concatenate AI's response as chunks:
		user_session.chunks = ""

		try:
			await ui.context.client.connected() # necessary?
			await ui.run_javascript('scrollable.scrollTo(0, scrollable.scrollHeight)')
		except Exception as e:
			pass # no biggie, if can't scroll then user can do it manually

		#                  ************
		async for chunk in run_streamer(user_session.provider, user_session.chat_history):
		#                  ************
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

			def reload_app(user_session):
				# ui.run_javascript("setTimeout(function() { window.location.replace(window.location.href); }, 1000);")
				ui.run_javascript("location.reload(true);")
				# ui.run_javascript("""
				# if (navigator.userAgent.includes('Safari') && !navigator.userAgent.includes('Chrome')) {
				#     setTimeout(function() { location.reload(true); }, 1000);
				# } else {
				#     setTimeout(function() { window.location.replace(window.location.href); }, 1000);
				# }
				# """)

			ui.button(icon='restart_alt', on_click=lambda: reload_app(user_session)) \
			.tooltip("Reload app") \
			.props('no-caps flat fab-mini')

			ui.button(
				icon='logout',
				on_click=lambda: app.shutdown()
			) \
			.tooltip("Shutdown app") \
			.props('no-caps flat fab-mini')


	#########################################################################
	# app's heart and raison d'etre ...
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


def main():
	global args, user_session

	user_session = UserSession()

	parser = setup_argument_parser()
	args = parser.parse_args()
	
	print("=" * 80)
	print("Current Claude Settings:")
	print(f"Thinking Budget: {args.thinking_budget} tokens")
	print(f"Max Output Tokens: {args.max_tokens}")
	print(f"No Markdown: {'true' if args.no_markdown else 'false'}")
	if args.chat_history:
		print(f"Continuing chat from: {args.chat_history}")
	print("=" * 80)
	
	if args.chat_history:
		print(f"Loading chat history from: {args.chat_history}")
		user_session.chat_history =  = load_chat_history(args.chat_history)

	ui.run(
		title="Thinking Claude",
		reload=False,
		dark=True,
		favicon="static/apikeys.png",
	)


if __name__ in {"__main__", "__mp_main__"}:
	main()

