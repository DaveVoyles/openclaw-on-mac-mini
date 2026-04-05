# Security Policy

## Supported Versions

We actively maintain and provide security updates for the following versions of OpenClaw:

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

We take the security of OpenClaw seriously. If you discover a security vulnerability, please follow these guidelines:

### How to Report

**DO NOT** create a public GitHub issue for security vulnerabilities.

Instead, please report security issues by emailing:

📧 **security@example.com**

Please include the following information:

- Description of the vulnerability
- Steps to reproduce the issue
- Potential impact
- Suggested fix (if any)
- Your contact information

### What to Expect

1. **Acknowledgment**: You will receive an acknowledgment within 48 hours.
2. **Assessment**: We will investigate and assess the vulnerability within 7 days.
3. **Updates**: We will keep you informed of our progress.
4. **Resolution**: We aim to resolve critical vulnerabilities within 30 days.
5. **Credit**: With your permission, we will credit you in the security advisory.

## Security Update Process

### Notification

When a security vulnerability is identified:

1. We create a private security advisory
2. We develop and test a fix
3. We release a patch version
4. We publish a security advisory
5. We notify users through:
   - GitHub Security Advisories
   - Release notes
   - Email notifications (for critical issues)

### Update Installation

To update to the latest secure version:

```bash
git pull origin main
pip install -r requirements.txt --upgrade
# Restart the bot
```

## Known Security Considerations

### API Keys and Credentials

- **Storage**: API keys are stored in `.env` files (never committed to git)
- **Rotation**: Rotate API keys regularly (recommended: every 90 days)
- **Access**: Use environment variables for all sensitive credentials
- **Scoping**: Use least-privilege access for API keys

### Authentication

- **Discord Bot Token**: Keep your Discord bot token secure
- **User Authentication**: Only allowed users can execute commands (see `permissions.py`)
- **Service Authentication**: API services require proper authentication

### Data Protection

#### Encryption at Rest

- Conversation history stored in local SQLite database
- Vector embeddings stored in ChromaDB
- No encryption at rest by default (recommended for production deployments)

**To enable encryption at rest:**
1. Use encrypted filesystem (e.g., LUKS, FileVault, BitLocker)
2. Encrypt database backups
3. Use encrypted storage for logs

#### Encryption in Transit

- Discord API: TLS 1.2+
- External APIs: HTTPS required
- Webhook endpoints: HTTPS recommended

### Input Validation

- **Command Input**: All user commands are validated before execution
- **File Uploads**: File size limits enforced (see `MAX_FILE_SIZE`)
- **URL Validation**: URLs are validated before browsing
- **Injection Prevention**: Parameterized queries for database operations

### Rate Limiting

- **Discord Commands**: Respect Discord rate limits (50 requests/second)
- **LLM API**: Rate limiting per `llm_ratelimit.py`
- **External APIs**: Provider-specific rate limits enforced

### Network Security

- **Outbound Connections**: Bot makes connections to:
  - Discord API (`discord.com`)
  - Google Gemini API (`generativelanguage.googleapis.com`)
  - External APIs (configurable)
- **Inbound Connections**: Health check endpoint on port 8080
- **Firewall**: Configure firewall to restrict access to health endpoint

### Code Execution

- **Subprocess Execution**: Limited to allowed commands (see `subprocess_utils.py`)
- **Code Sandbox**: Isolated execution environment (see `code_sandbox.py`)
- **Shell Injection**: Input sanitization prevents shell injection

### Dependencies

- **Regular Updates**: Update dependencies monthly
- **Vulnerability Scanning**: Use `pip-audit` or `safety`
- **Pinned Versions**: Dependencies are pinned in `requirements.txt`

**Scan for vulnerabilities:**

```bash
pip install pip-audit
pip-audit
```

### Logging and Monitoring

- **Audit Trail**: All commands logged (see `audit.py`)
- **Error Tracking**: Errors tracked and aggregated (see `error_tracker.py`)
- **Sensitive Data**: Credentials are redacted from logs
- **Log Rotation**: Logs rotated daily, retained for 30 days

## Security Best Practices

### Deployment

1. **Environment Variables**: Use `.env` file (never commit to git)
2. **File Permissions**: Restrict access to configuration files (chmod 600)
3. **User Isolation**: Run bot as non-root user
4. **Network Isolation**: Deploy in isolated network segment
5. **Backup**: Regular backups of database and configuration

### Development

1. **Code Review**: All changes reviewed before merge
2. **Static Analysis**: Use `mypy`, `ruff` for code quality
3. **Testing**: Comprehensive test coverage (see `tests/`)
4. **Secrets**: Never commit secrets to repository

### Operations

1. **Monitoring**: Monitor bot health and performance
2. **Alerting**: Configure alerts for errors and security events
3. **Incident Response**: Have an incident response plan
4. **Access Control**: Limit who can deploy and configure

## Security Architecture

### Components

```
┌─────────────────────────────────────────────────┐
│                  Discord API                    │
│              (TLS 1.2+ encrypted)               │
└─────────────────┬───────────────────────────────┘
                  │
                  │ Authenticated connection
                  │
┌─────────────────▼───────────────────────────────┐
│              OpenClaw Bot                       │
│  ┌───────────────────────────────────────────┐  │
│  │  Authentication & Authorization           │  │
│  │  - User whitelist (permissions.py)        │  │
│  │  - Service authentication                 │  │
│  └───────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────┐  │
│  │  Input Validation                         │  │
│  │  - Command validation                     │  │
│  │  - File size checks                       │  │
│  │  - URL validation                         │  │
│  └───────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────┐  │
│  │  Business Logic                           │  │
│  │  - Command handlers                       │  │
│  │  - LLM integration                        │  │
│  │  - API integrations                       │  │
│  └───────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────┐  │
│  │  Data Layer                               │  │
│  │  - SQLite (conversation history)          │  │
│  │  - ChromaDB (vector embeddings)           │  │
│  │  - File storage                           │  │
│  └───────────────────────────────────────────┘  │
└──────────────────┬──────────────────────────────┘
                   │
                   │ HTTPS (TLS 1.2+)
                   │
┌──────────────────▼──────────────────────────────┐
│           External Services                     │
│  - Google Gemini API                            │
│  - Weather APIs                                 │
│  - Email services                               │
│  - NAS/Docker hosts                             │
└─────────────────────────────────────────────────┘
```

### Trust Boundaries

1. **User Input**: All user input is untrusted
2. **External APIs**: API responses are validated
3. **File Uploads**: Files are scanned and validated
4. **Network Requests**: Only to whitelisted domains

## Responsible Disclosure Policy

We follow a coordinated disclosure process:

1. **Private Disclosure**: Report to security@example.com
2. **Assessment Period**: 7 days for initial assessment
3. **Fix Development**: Up to 30 days for critical issues
4. **Coordinated Release**: We coordinate disclosure with reporter
5. **Public Disclosure**: After fix is released (minimum 7 days notice)
6. **Credit**: We credit reporters in security advisories

### Disclosure Timeline

- **Day 0**: Vulnerability reported
- **Day 2**: Acknowledgment sent
- **Day 7**: Assessment complete
- **Day 30**: Fix released (critical issues)
- **Day 37**: Public disclosure

## Contact

For security concerns:
- Email: security@example.com
- PGP Key: [Not yet configured]

For general issues:
- GitHub Issues: https://github.com/yourusername/openclaw/issues

## References

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Discord Security Best Practices](https://discord.com/developers/docs/topics/oauth2#bot-authorization-flow)
- [Python Security Best Practices](https://python.readthedocs.io/en/stable/library/security_warnings.html)

---

**Last Updated**: 2024-04-05  
**Version**: 1.0
