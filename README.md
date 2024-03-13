### Cognitrix
Cognitrix is an open-source autonous AI agents orchestrator built in Python. It allows you to create and manage AI agents easily.

### Features
- **`Agent Creation and Management`:** Create, list, and load AI agents with customizable names, tasks, and configurations.
- **`LLM Integration`:** Seamlessly integrate with multiple LLM providers, including Anthropic (Claude), Cohere, Groq, Google, OpenAI, and Together.
- **`Modular Architecture`:** Easily extend the framework by adding new tools, agents, and LLM integrations.
- **`Conversational Interface`:** Interact with AI agents through a command-line interface, providing queries and receiving responses.
- **`Tool Integration`:** Agents can utilize a variety of tools, such as calculators, web searches, file system browsers, and more.
- **`Autonomous Agent Mode`:** Enable agents to operate autonomously, visually perceiving the screen, interacting with UI elements, and performing tasks.
- **`Multimodal Support`:** Agents can handle both text and image inputs/outputs, enabling multimodal interactions.

### Architecture
Cognitrix's architecture is designed to be modular and extensible, with core components including:

1. **`Agents`:** The base Agent class and specialized classes like AIAssistant for creating and managing AI agents.
2. **`LLMs`:** A collection of classes for integrating with various LLM providers, such as Cohere, OpenAI, Claude, and more.
3. **`Tools`:** A set of tools that agents can utilize, including calculators, web searches, file system browsers, and more.
4. **`Templates`:** Customizable prompt templates for guiding the behavior and output formats of LLMs.

The architecture is highly modular and extensible. New tools and capabilities can be easily added.

### Getting Started

### Installation
```bash
pip install cognitrix
```
or
```bash
pip install https://github.com/theonlyamos/cognitrix/archive/main.zip
```

### Usage
Run with default settings
```bash
cognitrix
```

List supported platforms
```bash
cognitrix --platforms
```

Run with specific platform
```bash
cognitrix --platform <platform_name>
```

Create a new agent
```bash
cognitrix agents --new
```

List created agents
```bash
cognitrix agents
```

Run with specific agent
```bash
cognitrix --agent <agent_name>
```

Print help message
```bash
cognitrix --help
```

### Contributing
Cognitrix is open source and contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for more details.

### License
This project is licensed under the MIT license. See [LICENSE.md](LICENSE.md) for more details.

### Acknowledgments
Cognitrix was created by [Amos Amissah](https://github.com/theonlyamos) and is heavily inspired by projects like AutoGPT and EngineerGPT. Special thanks to the open-source community for their contributions and the AI companies providing LLM APIs.