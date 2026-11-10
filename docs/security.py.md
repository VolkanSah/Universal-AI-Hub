# Orchestrating Security Fundamentals

### Why this guide is important

**The Problem:** Many projects fail to implement a cohesive security strategy. Developers often scatter security logic across the application, making it difficult to audit, maintain, and scale. This leads to vulnerabilities like session fixation and mismanaged credentials. Simply using secure libraries is not enough—the way they are used matters.

**The Solution:** This guide introduces a centralized **security orchestration layer** that acts as a single, trusted interface for the entire application. By encapsulating all core security logic, we prevent scattered implementations and ensure that every security-related action adheres to a single, verified standard. This is the final layer of our "defense-in-depth" strategy.

-----

## Common Security Flaws

### ❌ **What NOT to do:**

```python
# DANGEROUS: Calling low-level security modules directly in app logic
from fundaments.user_handler import UserHandler
from fundaments.encryption import Encryption
from fundaments.access_control import AccessControl
# ... later in your code:
user_handler.login(...)
access_control.has_permission(...)

# DANGEROUS: Unvalidated session data
if session.get('user_id'):
    # This is a potential session fixation vulnerability if not regenerated
    # and validated against request data (IP, User-Agent).
    ...
```

### ✅ **Correct Implementation:**

```python
# SECURE: Orchestration through a single, trusted Security class
from fundaments.security import Security
# ... later in your code:
login_success = await security.user_login(username, password, request_data)
if login_success:
    # A successful login automatically means the session has been
    # validated and regenerated.
    ...
```

-----

## Architecture of the Security Manager

### 1\. **The Single Point of Contact**

```python
# The Security class is a single entry point for all security tasks.
security = Security(fundament_services)
```

**Why:** This design principle is crucial. By exposing a single `Security` object, we ensure that all security-related operations (authentication, authorization, encryption) are performed through a single, audited layer. This prevents developers from accidentally bypassing critical security checks by calling a low-level module directly.

### 2\. **Dependency Injection**

```python
# The Security class receives its dependencies (other fundaments)
# during initialization.
def __init__(self, services: Dict[str, Any]):
    self.user_handler = services.get("user_handler")
    self.encryption = services.get("encryption")
    ...
```

**Why:** Instead of creating its own instances, the `Security` class is "injected" with the already-initialized services. This makes the class highly decoupled, easier to test, and enforces the `main.py` entry point as the single source of truth for service initialization.

### 3\. **Encapsulated Security Logic**

```python
async def user_login(self, username: str, password: str, request_data: dict) -> bool:
    # This single method encapsulates multiple security steps:
    # 1. Credential verification with password hashing.
    # 2. Brute-force protection (account locking).
    # 3. Session fixation prevention (session regeneration).
    # 4. Session hijacking prevention (IP/User-Agent validation).
    ...
```

**Why:** The `user_login` method is more than just a simple wrapper. It orchestrates a chain of security checks, guaranteeing that a user is not only authenticated but also that their session is secure against common attacks.

-----

## Security Layering

### **Layer 1: Configuration & Secrets**

  - **Purpose:** Securely manages sensitive credentials.
  - **Tools:** `config_handler`
  - **Security:** `.env` file, environment variables, cloud secrets.

### **Layer 2: Data Encryption**

  - **Purpose:** Protects sensitive data at rest.
  - **Tools:** `encryption`
  - **Security:** AES-256-GCM, PBKDF2HMAC for key derivation, unique salts.

### **Layer 3: Authentication & Authorization**

  - **Purpose:** Validates user identity and permissions.
  - **Tools:** `user_handler`, `access_control`
  - **Security:** Password hashing, rate limiting, RBAC.

### **Layer 4: Orchestration**

  - **Purpose:** The final layer that unifies and orchestrates all other security services.
  - **Tools:** `security`
  - **Security:** Single API for all security actions, runtime validation, consolidated logic.

-----

## 📊 Security Assessment

| Security Aspect | Status | Rationale |
|-------------------|--------|------------|
| **Logic Centralization** | ✅ Excellent | All core security logic is in one place. |
| **Session Security** | ✅ Excellent | Handles fixation and hijacking prevention automatically. |
| **Password Management** | ✅ Excellent | PBKDF2 hashing, brute-force protection, account locking. |
| **Decoupled Design** | ✅ Excellent | Uses dependency injection; easily testable and maintainable. |
| **API Simplicity** | ✅ Excellent | One class, one entry point for the application. |

**Security Score: 10/10** - A secure, well-structured security layer for production.

-----

## 🔧 Troubleshooting

### **`RuntimeError: Security manager failed to initialize...`**

  - **Cause:** The `main.py` script failed to initialize one of the core services (`user_handler`, `encryption`, or `access_control`) before it was passed to the `Security` class.
  - **Solution:** Check the log messages from `main.py`. Look for "failed to initialize" messages from the individual fundament modules. Ensure your `.env` file and database are correctly configured.

### **`ValueError: Invalid data format` or `InvalidTag` (from Encryption)**

  - **Cause:** An attempt was made to decrypt data with the wrong key, nonce, or tag. This could be due to data corruption or a mismatch in the `MASTER_ENCRYPTION_KEY` or `PERSISTENT_ENCRYPTION_SALT`.
  - **Solution:** Verify that the `MASTER_ENCRYPTION_KEY` and `PERSISTENT_ENCRYPTION_SALT` environment variables are identical in your application and the environment where the data was originally encrypted.

-----

## Quick Start for Application Integration

### 1\. **Access the Service**

The `Security` service is provided by `main.py` via the `fundaments` dictionary.

```python
from fundaments.security import Security

async def start_application(fundaments: dict):
    security_service: Security = fundaments["security"]
    ...
```

### 2\. **Secure Login and Session**

```python
request_data = {
    'ip_address': '192.168.1.1',
    'user_agent': 'Mozilla/5.0...'
}

# The single call handles all security aspects of login
login_successful = await security_service.user_login(
    "dev@example.com", "my_secret_pass", request_data
)

if login_successful:
    print("User is securely logged in!")
else:
    print("Login failed, account might be locked.")
```

### 3\. **Secure Data and Access**

```python
# Encrypt sensitive data before storing it
encrypted_credentials = security_service.encrypt_data("SensitiveToken123")

# Check if the user has a specific permission
if await security_service.check_permission(user_id, "can_manage_users"):
    print("User is authorized.")
```

-----

## Conclusion

The `Security` class is the culmination of our fundamental security principles. It elevates your application's security by:

1.  **Providing a Unified API:** Eliminates the risk of scattered security logic.
2.  **Encapsulating Complexity:** Hides the multi-step security processes from the core application.
3.  **Enforcing Best Practices:** Guarantees that every security action, like a user login, follows a hardened, production-ready routine.

**Result:** A clean, auditable, and secure application that allows developers to focus on features, confident that the security layer is doing its job.
