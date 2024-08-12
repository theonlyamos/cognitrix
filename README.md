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

**Install with pip**:

```bash
pip install cognitrix
```

**Build from source**:

```bash
git clone https://github.com/theonlyamos/cognitrix.git
cd cognitrix/frontend
npm install
npm run dev
cd ..
pip install .
```

**Install directly from github**:

```bash
pip install https://github.com/theonlyamos/cognitrix/archive/main.zip
```

**Build the Docker Image**:

```bash
git clone https://github.com/theonlyamos/cognitrix.git
cd cognitrix
docker build -t cognitrix .
```

## Usage

**Fill these environment variables as needed**

```bash
OPENAI_API_KEY=
CO_API_KEY=
TAVILY_API_KEY=
CLARIFAI_ACCESS_TOKEN=
GROQ_API_KEY=
GOOGLE_API_KEY=
NEWSAPI_API_KEY=
ANTHROPIC_API_KEY=
DEEPGRAM_API_KEY=
MINDSDB_API_KEY=
BRAVE_SEARCH_API_KEY=
AIMLAPI_API_KEY=
```

To run Cognitrix with default settings:

```bash
cognitrix
```

**Access the Web UI**

To run with web interface

```bash
cognitrix --ui web
```

Open your web browser and go to `http://localhost:8000` to access the web UI.

**Run the Docker Container**

After building the image, you can run the container with:

```bash
docker run -p 8000:8000 cognitrix
```

This command maps port 8000 of the container to port 8000 on your host machine.

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

## Web UI

Cognitrix includes a web UI built with Svelte and TypeScript. This UI allows users to interact with the AI agents and manage their configurations easily.

### Development

For development purposes, you can also run the web UI locally without Docker. Ensure you have Node.js and npm installed, then follow these steps:

1. **Install Dependencies**:
   Navigate to the `frontend` directory and run:

   ```bash
   npm install
   ```

2. **Start the Development Server**:
   Run the following command to start the development server:

   ```bash
   npm run dev
   ```

3. **Access the Development Server**:
   Open your web browser and go to `http://localhost:5173` to view the web UI in development mode.

## Contributing

Cognitrix is open source and contributions are welcome! Please refer to [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to contribute.

## License

This project is licensed under the MIT license. See [LICENSE.md](LICENSE.md) for more information.

## Acknowledgments

Cognitrix was created by [Amos Amissah](https://github.com/theonlyamos) and is inspired by projects like AutoGPT and GPTEngineer. Special thanks to the open-source community and AI companies providing LLM APIs.
