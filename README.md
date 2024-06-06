# Cognitrix

Cognitrix is an open-source autonomous AI agents orchestrator built in Python. It allows you to create and manage AI agents with ease and integrates seamlessly with large language models (LLMs) from various providers. 

## Features

- **Agent Creation and Management:** Create, list, and load AI agents with customizable names, tasks, and configurations.
- **LLM Integration:** Integrates with multiple LLM providers, including Anthropic (Claude), Cohere, Groq, Google, OpenAI, and Together.
- **Modular Architecture:** Easily extensible, allowing the addition of new tools, agents, and LLM integrations.
- **Conversational Interface:** Interact with AI agents through a command-line interface, providing queries and receiving responses.
- **Tool Integration:** Agents can utilize a variety of tools, including calculators, web searches, file system browsers, and more.
- **Autonomous Agent Mode:** Agents can operate autonomously, visually perceiving the screen, interacting with UI elements, and performing tasks.
- **Multimodal Support:** Handles both text and image inputs/outputs, enabling multimodal interactions.

## Architecture

Cognitrix's architecture is designed to be highly modular and extensible:

- **Agents:** The base `Agent` class and specialized classes like `AIAssistant` for creating and managing AI agents.
- **LLMs:** A collection of classes for integrating with various LLM providers (Cohere, OpenAI, Claude, etc.).
- **Tools:** A set of tools that agents can utilize. Each tool has a `category` attribute for grouping and management.
- **Templates:** Customizable prompt templates guide the behavior and output formats of LLMs.

More tools can be added by creating new classes that inherit from the `Tool` base class and specifying a unique `category`.

## Installation

```bash
pip install cognitrix
```

Alternatively, you can install directly from GitHub:

```bash
pip install https://github.com/theonlyamos/cognitrix/archive/main.zip
```

## Usage

To run Cognitrix with default settings:

```bash
cognitrix
```

To list supported LLM providers:

```bash
cognitrix --providers
```

To list created agents:

```bash
cognitrix agents
```

To list available tools:

```bash
cognitrix --tools
```

To run Cognitrix with a specific provider:

```bash
cognitrix --provider <provider_name>
```

To run Cognitrix with a specific agent:

```bash
cognitrix --agent <agent_name>
```

To run Cognitrix with a category of tools:

```bash
cognitrix --load-tools "web"
```

To run Cognitrix with categories of tools:

```bash
cognitrix --load-tools "web,general"
```

To create a new agent:

```bash
cognitrix agents --new
```

For more options and usage details, use the help command:

```bash
cognitrix --help
```

## Contributing

Cognitrix is open source and contributions are welcome! Please refer to [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to contribute.

## License

This project is licensed under the MIT license. See [LICENSE.md](LICENSE.md) for more information.

## Acknowledgments

Cognitrix was created by [Amos Amissah](https://github.com/theonlyamos) and is inspired by projects like AutoGPT and GPT Engineer. Special thanks to the open-source community and AI companies providing LLM APIs.