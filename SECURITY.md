# Security Policy

## Supported versions

Security fixes target the latest tagged release and the current default branch.
Older releases may not receive backports.

## Report a vulnerability

Use GitHub's private vulnerability reporting for this repository when it is
available. Do not open a public issue with exploit details, credentials, private
URLs, cookies, model inputs, or personal documents.

If private reporting is not enabled, open a minimal issue that contains no
sensitive details and asks the maintainer to establish a private coordination
channel. Do not attach proof-of-concept data publicly.

Useful private reports include:

- the affected component and version or commit;
- a minimal reproduction with sanitized inputs;
- the security impact and required preconditions;
- suggested mitigation, if known.

Issues involving command execution, path handling, local file disclosure,
credential exposure, unsafe browser behavior, or unauthenticated network
binding are in scope. Vulnerabilities in downloaded models or third-party
dependencies should also be reported upstream when appropriate.
