# Thinking Claude

A web-based interface for Claude 3.7 Sonnet that unlocks the AI's powerful "thinking" capability, providing unprecedented insight into Claude's reasoning process.

## What is Thinking Claude?

Thinking Claude is a Python application that creates a custom web interface for interacting with Anthropic's Claude 3.7 Sonnet AI model. Unlike standard interfaces, this tool exposes Claude's "thinking" capability—allowing you to see the model's internal reasoning process before it provides its final answer.

The application leverages Anthropic's beta features to provide:

- Access to Claude's thinking process with a configurable token budget (32,000 tokens by default)
- Support for the full 204,648 token context window
- Real-time streaming of both thinking and responses
- A clean, intuitive web interface for extended conversations

## Why Use Thinking Claude?

Standard Claude interfaces don't expose the model's thinking process, which happens behind the scenes. Thinking Claude bridges this gap by:

1. Revealing how Claude approaches complex problems step by step
2. Showing how the AI considers multiple perspectives before settling on an answer
3. Demonstrating Claude's explicit reasoning methodology
4. Exposing potential confusions or uncertainties in the model's approach

Most importantly, the thinking process gets its own separate token budget, meaning Claude can think extensively without sacrificing the length of its final response.

## Installation

### Prerequisites
- Python 3.8 or higher
- An Anthropic API key with access to Claude 3.7 Sonnet

### Steps

1. Download the repository:
   - Visit the GitHub repository page
   - Click the green "Code" button near the top of the page
   - Select "Download ZIP" from the dropdown menu
   - Extract the downloaded ZIP file to your preferred location

2. Install the required dependencies:
```
pip install -r requirements.txt
```

3. Set your Anthropic API key as an environment variable:
```
# On Linux/Mac
export ANTHROPIC_API_KEY=your_api_key_here

# On Windows
set ANTHROPIC_API_KEY=your_api_key_here
```

## Usage

Start the application with default settings:
```
python thinking_claude.py
```

This will launch a web server that you can access at http://localhost:8080 in your browser.

### Command Line Options

Customize Thinking Claude's behavior with these command line arguments:

```
python thinking_claude.py [OPTIONS]

Options:
  --request_timeout INT     Maximum timeout for each streamed chunk (default: 300 seconds)
  --thinking_budget INT     Maximum tokens for AI thinking (default: 32000)
  --max_tokens INT          Maximum tokens for output (default: 12000)
  --context_window INT      Context window size (default: 204648)
  --save_dir STR            Directory to save output files (default: current directory)
  --chat_history STR        Optional chat history file to continue a conversation
  --no_markdown             Tell Claude not to respond with Markdown formatting
```

## Features

### Core Capabilities

- **Thinking Process Visualization**: See Claude's complete reasoning process as it happens
- **Separate Token Budgets**: The thinking process has its own token budget (32K tokens by default)
- **Beta API Access**: Leverages Anthropic's beta features including "output-128k-2025-02-19"
- **Real-time Streaming**: Both thinking and responses stream in real-time for an interactive experience

### User Interface

- **Clean Web Interface**: Built with NiceGUI for a responsive, modern UI experience
- **Dark/Light Mode**: Toggle between visual themes based on your preference
- **Chat History Management**: Save, load, and clear conversations
- **Copy to Clipboard**: Easily copy individual messages or the entire conversation
- **Download History**: Save your conversations as text files

## How It Works

Thinking Claude integrates with Anthropic's API using the following approach:

1. Your prompt is sent to Claude 3.7 Sonnet through the Anthropic API
2. The "thinking" capability is enabled with a separate token budget
3. Both thinking output and final responses are streamed in real-time
4. The interface clearly separates thinking from final responses

The technical implementation uses the `beta.messages.stream` endpoint with the "thinking" parameter enabled:

```python
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
    # Process thinking and response streams
```

## Use Cases

Thinking Claude is particularly valuable for:

- **Complex Problem Solving**: Observe how Claude breaks down difficult problems
- **Educational Purposes**: Learn from Claude's reasoning process
- **Research**: Gain insights into how Claude approaches various topics
- **Content Creation**: See how Claude organizes thoughts before crafting responses
- **Decision Support**: Make more informed judgments about Claude's conclusions



## Acknowledgments

- Built with [NiceGUI](https://nicegui.io/) for the web interface
- Powered by [Anthropic's Claude API](https://docs.anthropic.com/claude/reference/getting-started-with-the-api)

---

*Note: This tool is not officially affiliated with Anthropic. It is an independent project that uses the Anthropic API.*
