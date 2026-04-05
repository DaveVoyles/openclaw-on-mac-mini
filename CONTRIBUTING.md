# Contributing to OpenClaw

Thank you for your interest in contributing to OpenClaw! This guide will help you get started with development, testing, and submitting changes.

## Development Setup

### Prerequisites
- Python 3.12+
- Git
- Discord bot token (for integration testing)

### Initial Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/openclaw.git
   cd openclaw
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt -r requirements-test.txt
   ```

4. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys and configuration
   ```

## Running Tests

### Basic Test Execution

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_digest_manager.py -v

# Run specific test
pytest tests/test_digest_manager.py::test_specific_function -v

# Run tests matching a pattern
pytest tests/ -k "digest" -v
```

### Coverage Reports

```bash
# Generate coverage report
pytest tests/ --cov=src --cov-report=html

# View coverage in browser
open htmlcov/index.html  # macOS/Linux
start htmlcov/index.html  # Windows

# Terminal coverage summary
pytest tests/ --cov=src --cov-report=term-missing
```

### Parallel Execution

Tests run in parallel by default using `pytest-xdist`:

```bash
# Automatic core detection (default)
pytest tests/ -n auto

# Specify number of workers
pytest tests/ -n 4

# Disable parallel execution
pytest tests/ -n 0
```

### Test Markers

Use markers to run specific test categories:

```bash
# Run only integration tests
pytest tests/ -m integration

# Skip slow tests
pytest tests/ -m "not slow"

# Run tests that don't require secrets
pytest tests/ -m "not requires_secrets"
```

Available markers:
- `slow`: Tests that take more than 2 seconds
- `integration`: Integration tests with multiple components
- `requires_secrets`: Tests requiring API keys
- `requires_docker`: Tests requiring Docker

### Flaky Test Handling

Tests automatically retry up to 2 times with a 1-second delay. To disable:

```bash
pytest tests/ --reruns 0
```

### Test Duration Analysis

```bash
# Show 10 slowest tests
pytest tests/ --durations=10

# Show all test durations
pytest tests/ --durations=0
```

## Code Quality

### Linting

We use Ruff for fast linting and formatting, and pre-commit hooks to ensure code quality.

```bash
# Install pre-commit hooks (one-time setup)
pip install pre-commit
pre-commit install

# Check for issues
ruff check src/ tests/

# Auto-fix issues
ruff check src/ tests/ --fix

# Format code
ruff format src/ tests/

# Run all pre-commit hooks manually
pre-commit run --all-files
```

**Pre-commit hooks automatically run on every commit:**
- Ruff linting and formatting
- MyPy type checking (on configured files)
- Trailing whitespace removal
- YAML/JSON/TOML validation
- Large file detection
- Private key detection

To skip hooks temporarily (not recommended):
```bash
git commit --no-verify
```

### Type Checking

All new code must include type annotations for better code quality and IDE support.

```bash
# Run mypy type checker
mypy src/

# Check specific files
mypy src/config.py src/digest_manager.py

# With stricter settings (for new code)
mypy src/ --strict
```

#### Type Annotation Standards

**Required for all new code:**

```python
# ✅ Good - Full type annotations
from openclaw_types import JSON, SkillResult

def process_data(items: list[str], options: dict[str, int]) -> dict[str, Any]:
    """Process data with proper types."""
    ...

async def fetch_api(url: str, timeout: int = 30) -> JSON:
    """Fetch data from API."""
    ...

# ❌ Bad - Missing type hints
def process_data(items, options):
    ...

def process_data(data):  # Missing types
    ...

def process_data(data: Any) -> Any:  # Too generic
    ...
```

**Use specific types, not generic:**

```python
# ✅ Good - Specific types
def parse_config(data: dict[str, str | int]) -> list[tuple[str, int]]:
    ...

# ❌ Bad - Generic/missing types
def parse_config(data: dict) -> list:  # Missing type parameters
    ...
```

**Modern syntax (Python 3.10+):**

```python
# ✅ Good - Modern union syntax
def get_user(user_id: str) -> User | None:
    ...

# ❌ Outdated - Don't use Optional
from typing import Optional
def get_user(user_id: str) -> Optional[User]:
    ...
```

**Use common types from `openclaw_types`:**

```python
from openclaw_types import (
    JSON,
    UserID,
    ChannelID,
    SkillResult,
    MessageContext,
    NewsArticle,
    WeatherData,
)

async def get_weather(location: str, user_id: UserID) -> SkillResult:
    """Get weather with standard result type."""
    return {
        "status": "success",
        "data": weather_data,
        "message": "Weather retrieved successfully"
    }
```

**Avoid these patterns:**

```python
# ❌ Too broad - be specific
data: Any
items: list  # Use list[str], list[int], etc.
config: dict  # Use dict[str, Any], dict[str, int], etc.

# ❌ Wrong import style (use | instead)
from typing import Optional, Union
value: Optional[str]  # Use: str | None
result: Union[str, int]  # Use: str | int
```

**Type checking files:**

Priority files (must pass mypy strict):
- `src/openclaw_types.py` - Common type definitions
- `src/config.py` - Configuration
- `src/digest_manager.py` - Digest management
- `src/trend_tracker.py` - Trend tracking
- New files you create

See `pyproject.toml` `[tool.mypy]` section for configuration.

### Security Scanning

```bash
# Scan for security issues
bandit -r src/

# Check dependencies for vulnerabilities
safety check
```

## Pull Request Process

### Before Submitting

1. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**
   - Write tests for new features
   - Update documentation as needed
   - Follow existing code style

3. **Run the full test suite**
   ```bash
   pytest tests/ -v
   ruff check src/ tests/
   mypy src/
   ```

4. **Commit with conventional commits**
   ```
   feat: Add new digest scheduling feature
   fix: Resolve timezone handling in scheduler
   docs: Update API documentation
   test: Add integration tests for LLM gateway
   refactor: Simplify digest manager logic
   ```

5. **Push and create PR**
   ```bash
   git push origin feature/your-feature-name
   # Create PR on GitHub
   ```

### PR Guidelines

- Write clear, descriptive PR titles
- Include a detailed description of changes
- Reference related issues (e.g., "Fixes #123")
- Ensure CI passes before requesting review
- Respond to review feedback promptly
- Keep PRs focused and reasonably sized

## Testing Standards

### Coverage Requirements

- **Overall project**: 50% minimum (incremental target)
- **New code**: 80% minimum (enforced by Codecov)
- **Critical paths**: 100% recommended

### Test Structure

```python
"""Module docstring describing what's being tested."""
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_feature_name():
    """Test description in docstring."""
    # Arrange
    mock_dependency = AsyncMock()
    
    # Act
    result = await function_under_test(mock_dependency)
    
    # Assert
    assert result is not None
    mock_dependency.method.assert_called_once()
```

### Test Categories

1. **Unit Tests** - Test individual functions/classes
2. **Integration Tests** - Test component interactions
3. **End-to-End Tests** - Test complete workflows

### Best Practices

- Use pytest fixtures for common setup
- Mock external API calls
- Test both success and error cases
- Use descriptive test names
- Keep tests focused and independent
- Avoid test interdependencies

### Writing Integration Tests

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_workflow():
    """Test complete workflow from start to finish."""
    # Setup test environment
    # Execute workflow
    # Verify all steps completed
    pass
```

## Development Workflow

### Daily Development

```bash
# Update your branch
git pull origin main

# Make changes
# ... edit files ...

# Run quick tests
pytest tests/test_your_module.py -v

# Run full suite before committing
pytest tests/ -v
ruff check src/ tests/

# Commit and push
git add .
git commit -m "feat: Your feature description"
git push
```

### Debugging Tests

```bash
# Run with verbose output
pytest tests/ -vv

# Stop at first failure
pytest tests/ -x

# Drop into debugger on failure
pytest tests/ --pdb

# Print output during tests
pytest tests/ -s
```

## Project Structure

```
openclaw/
├── src/              # Main application code
├── tests/            # Test files
├── config/           # Configuration files
├── skills/           # Bot skills/plugins
├── docs/             # Documentation
├── scripts/          # Utility scripts
└── examples/         # Example configurations
```

## Getting Help

- Open an issue for bugs or feature requests
- Check existing issues for similar questions
- Join our Discord community (link)
- Read the documentation in `/docs`

## Code of Conduct

- Be respectful and professional
- Provide constructive feedback
- Help others learn and grow
- Follow the principle of "no surprises"

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Happy coding! 🤖
