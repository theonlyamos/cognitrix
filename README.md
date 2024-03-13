### Spiral
Spiral is an open-source autonous AI agents orchestrator built in Python. It allows you to create and manage AI agents easily.

### Features
- Create and manage AI agents.
- Conversational interface using chat
- Modular architecture for easy extensibility
- Access to various tools like calculators, web - search, etc.
- Support for multiple platforms

### Architecture
The core components of Spiral are:

- `Agent` - Base class for chatbot agents with - support for tools
- `LLM` - Integration with large language models like - Cohere, GPT-3 etc.
- `Tools` - Various utility tools like calculators, search etc.
- `Memory` - For tracking context and state

The architecture is highly modular and extensible. New tools and capabilities can be easily added.

### Getting Started

### Installation
```bash
pip install spiral
```
or
```bash
pip install https://github.com/theonlyamos/spiral/archive/main.zip
```

### Usage
Run with default settings
```bash
spiral
```

List supported platforms
```bash
spiral --platforms
```

Run with specific platform
```bash
spiral --platform <platform_name>
```

Create a new agent
```bash
spiral agents --new
```

List created agents
```bash
spiral agents
```

Run with specific agent
```bash
spiral --agent <agent_name>
```

Print help message
```bash
spiral --help
```

### Contributing
Spiral is open source and contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for more details.

### License
This project is licensed under the MIT license. See [LICENSE.md](LICENSE.md) for more details.

### Credits
Spiral was created by [Amos Amissah](https://github.com/theonlyamos).