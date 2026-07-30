import os
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

# Generate RSA 2048 key pair
private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048
)

# Export Private Key in PEM format (PKCS#8)
private_pem = private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
).decode('utf-8')

# Export Public Key in PEM format (SubjectPublicKeyInfo)
public_pem = private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
).decode('utf-8')

# Save private key to bybit_rsa_private.pem
key_path = os.path.abspath("bybit_rsa_private.pem")
with open(key_path, "w", encoding="utf-8") as f:
    f.write(private_pem)

# Format single line without header/footer for Bybit text input if needed
raw_base64 = "".join(public_pem.strip().splitlines()[1:-1])

print("PRIVATE_KEY_SAVED_TO:", key_path)
print("\n--- STANDARD PEM PUBLIC KEY ---")
print(public_pem)
print("--- RAW BASE64 PUBLIC KEY ---")
print(raw_base64)
