import os
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

def generate_keypair():
    print("Generating 2048-bit RSA Key Pair for Bybit AI Subaccount...")
    
    # 1. Generate Private Key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048
    )
    
    # 2. Serialize Private Key to PEM format
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    # 3. Serialize Public Key to PEM format
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    private_key_file = os.path.abspath("bybit_rsa_private.pem")
    public_key_file = os.path.abspath("bybit_rsa_public.pem")
    
    with open(private_key_file, "wb") as f:
        f.write(private_pem)
        
    with open(public_key_file, "wb") as f:
        f.write(public_pem)
        
    print("[SUCCESS] RSA Key Pair generated!")
    print(f"Private Key saved to: {private_key_file}")
    print(f"Public Key saved to:  {public_key_file}\n")
    print("=" * 60)
    print("COPY THIS PUBLIC KEY TO BYBIT (AI Subaccount -> Create API Key):")
    print("=" * 60)
    print(public_pem.decode('utf-8'))
    print("=" * 60)

if __name__ == "__main__":
    generate_keypair()
