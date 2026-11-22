# Secure and Robust Encryption Module (AES-256-GCM)

### Overview

This module provides a secure and reusable encryption component for your application's `fundaments`. It is built on the industry-standard `cryptography` library and utilizes `AES-256-GCM`, an authenticated encryption mode that ensures both confidentiality and integrity of your data.

It is designed to be a "set-and-forget" core component, allowing developers to handle encryption without getting bogged down in low-level cryptographic details, while still adhering to best practices.

-----

### Key Security Concepts

This module's security is built upon the following principles:

1.  **AES-256-GCM:** The chosen algorithm is AES (Advanced Encryption Standard) with a 256-bit key, using GCM (Galois/Counter Mode). GCM is an authenticated encryption mode, meaning it not only encrypts data but also generates a unique **authentication tag** to verify that the data has not been tampered with.
2.  **Key Derivation (PBKDF2):** The actual encryption key is not the raw master key. Instead, it is derived from the master key and a unique **salt** using PBKDF2 with a high number of iterations. This makes brute-force attacks against the master key computationally infeasible.
3.  **Nonce/IV:** Each encryption operation generates a unique, random **nonce** (Number used once) to ensure that the same plaintext encrypted multiple times results in different ciphertext, protecting against pattern analysis.
4.  **Secure Storage:** The module emphasizes the importance of securely storing the **master key** (e.g., in environment variables) and the persistent **salt** (e.g., in a secure configuration file or database).

-----

### Usage

First, ensure the required library is installed: `pip install cryptography`.

#### 1\. **Initialization**

To use the module, you must provide a `master_key` and a persistent `salt`. The `salt` should be generated once and stored securely.

```python
from fundaments.encryption import Encryption

# Generate a salt ONCE and store it securely
# DO NOT generate a new salt on every run!
persistent_salt = Encryption.generate_salt() 

# Your master key should be stored in an environment variable or secret
master_key = os.getenv("MASTER_ENCRYPTION_KEY")

# Initialize the encryption handler
crypto_handler = Encryption(master_key=master_key, salt=persistent_salt)
```

#### 2\. **Encrypting and Decrypting Strings**

The `encrypt` method returns a dictionary containing the encrypted data, nonce, and tag. You must store all three to be able to decrypt the data later.

```python
# Encrypt a string
plaintext = "This is a secret message."
encrypted_data = crypto_handler.encrypt(plaintext)

# The output is a dict:
# {'data': '...', 'nonce': '...', 'tag': '...'}

# Decrypt the string
decrypted_string = crypto_handler.decrypt(
    encrypted_data['data'],
    encrypted_data['nonce'],
    encrypted_data['tag']
)

print(decrypted_string) # -> "This is a secret message."
```

#### 3\. **Encrypting and Decrypting Files**

The module also supports streaming file encryption, which is efficient for large files as it doesn't load the entire file into memory. The nonce and tag are automatically prepended and appended to the encrypted file.

```python
# Encrypt a file
metadata = crypto_handler.encrypt_file(
    source_path='my_secret_file.txt',
    destination_path='my_secret_file.txt.enc'
)

# Decrypt a file
crypto_handler.decrypt_file(
    source_path='my_secret_file.txt.enc',
    destination_path='my_decrypted_file.txt'
)
```

-----

### Security Checklist

  - [x] **Key Derivation:** Uses PBKDF2-HMAC-SHA256 with 480k+ iterations.
  - [x] **Authenticated Encryption:** Relies on AES-256-GCM, which provides integrity protection.
  - [x] **Nonce Usage:** A unique nonce is generated for every encryption operation.
  - [x] **Tamper Detection:** The `decrypt` method raises an `InvalidTag` exception if the ciphertext is modified.
  - [x] **Credential Management:** Encourages using environment variables for the master key.
  - [x] **File Streaming:** Supports file encryption for large files without memory overflow.

This module provides a strong, secure foundation for handling sensitive data within your application.
