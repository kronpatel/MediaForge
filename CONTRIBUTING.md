# Contributing to MediaForge

Thank you for your interest in contributing to MediaForge! Contributions from the community help make this project better for everyone.

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

---

## How Can I Contribute?

### 1. Reporting Bugs

If you find a bug, please check the existing issues first. If it hasn't been reported, open a new issue and include:
* A clear and descriptive title.
* Steps to reproduce the behavior.
* What you expected to happen vs. what actually happened.
* Relevant environment details (Python version, browser, operating system).
* Logs or error messages from the backend or browser console.

### 2. Suggesting Enhancements

We welcome suggestions for new features or improvements. When opening an enhancement issue, please describe:
* The goal of the enhancement.
* How it should work (user flow).
* Why this feature would be useful to the wider community.

### 3. Submitting Pull Requests

If you'd like to write code to fix a bug or implement a feature, please follow these steps:

1. **Fork the Repository**: Create a personal fork on GitHub.
2. **Clone Your Fork**:
   ```bash
   git clone https://github.com/YOUR_USERNAME/MediaForge.git
   cd MediaForge
   ```
3. **Create a Branch**: Use a clear naming convention.
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/bug-description
   ```
4. **Set Up the Environment**: Follow the setup instructions in the [README](README.md).
5. **Make Your Changes**: Adhere to clean coding style.
6. **Commit Your Changes**: Keep commits atomic and messages descriptive:
   ```bash
   git commit -m "Add feature support for X"
   ```
7. **Push to Your Fork**:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Open a Pull Request**: Submit a PR to the `main` branch of the official repository.

---

## Pull Request Guidelines

* **Keep it focused**: A pull request should do one thing. If you want to fix multiple unrelated issues, open separate PRs.
* **Update documentation**: If you introduce settings or features, make sure to document them in the `README.md`.
* **Clean Code**: Adhere to PEP 8 standards for Python and write modern, clean JavaScript/CSS.
* **Test your changes**: Verify that the extension UI works correctly and communication with the Flask backend is stable.

We appreciate all contributions, big or small!
