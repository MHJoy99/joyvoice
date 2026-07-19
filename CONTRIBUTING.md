# Contributing to JoyVoice

Thank you for your interest in contributing to JoyVoice! 🎤

## Getting Started

1. **Fork** the repository on GitHub.
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/joyvoice.git
   cd joyvoice
   ```
3. **Create a branch** for your change:
   ```bash
   git checkout -b feature/my-feature
   ```

## Development Setup

```bash
# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate   # Windows
# source venv/bin/activate  # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# Install development dependencies
pip install -r requirements-dev.txt
```

## Code Style

- Follow [PEP 8](https://peps.python.org/pep-0008/) for Python code.
- Use meaningful variable and function names.
- Keep functions small and focused.
- Add docstrings to all public functions and classes.
- Type hints are encouraged.

## Testing

- Write tests for new features and bug fixes.
- Run the test suite before submitting:
  ```bash
  pytest
  ```

## Pull Request Process

1. Ensure your code passes all tests and linting.
2. Update documentation if you change user-facing behavior.
3. Write a clear PR description — what, why, and how.
4. Reference any related issues (e.g., `Closes #42`).
5. Keep PRs focused on a single change.

## Reporting Bugs

Use the [Bug Report](https://github.com/MHJoy/joyvoice/issues/new?template=bug_report.md) template to report issues. Include:
- Steps to reproduce
- Expected vs actual behavior
- Environment details (OS, Python version, etc.)

## Feature Requests

Use the [Feature Request](https://github.com/MHJoy/joyvoice/issues/new?template=feature_request.md) template. Describe the problem you want to solve and your proposed solution.

## Code of Conduct

- Be respectful and inclusive.
- Provide constructive feedback.
- Focus on what is best for the project and its users.

## Questions?

Open a [Discussion](https://github.com/MHJoy/joyvoice/discussions) or ask in an issue.

---

Thanks for contributing! 🚀
