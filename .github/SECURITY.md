# Security Policy

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability in OpenClaw, please report it to:

📧 **security@example.com**

**Please do not create public GitHub issues for security vulnerabilities.**

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

### Response Timeline

- **48 hours**: Initial acknowledgment
- **7 days**: Vulnerability assessment
- **30 days**: Fix for critical issues

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | :white_check_mark: |
| < 1.0   | :x:                |

## Security Best Practices

### For Users

1. Keep your Discord bot token secure
2. Rotate API keys regularly
3. Use environment variables for credentials
4. Run with least-privilege access
5. Keep dependencies updated

### For Developers

1. Never commit secrets to git
2. Review all code changes
3. Run security scans (`pip-audit`)
4. Follow input validation guidelines

For full security documentation, see [SECURITY.md](../SECURITY.md) in the root directory.

---

**Contact**: security@example.com
