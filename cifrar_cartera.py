# -*- coding: utf-8 -*-
"""
CIFRA LA CARTERA PARA PUBLICARLA SIN EXPONERLA
==============================================
Lee cartera_plano.json (que NUNCA se sube: esta en .gitignore) y escribe
docs/cartera.enc.json, que si se sube al repo publico pero es ilegible sin
la contrasena.

Cifrado: PBKDF2-HMAC-SHA256 (310.000 iteraciones) + AES-256-GCM.
La pagina docs/cartera.html lo descifra en el navegador con Web Crypto.
La contrasena nunca sale de tu maquina ni de tu navegador.

La contrasena se toma, en este orden:
  1. del archivo cartera_pass.txt, si existe (tambien en .gitignore)
  2. preguntandola por teclado, sin mostrarla

Uso:
    python cifrar_cartera.py
"""
import base64
import getpass
import hashlib
import io
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).parent
PLANO = REPO / "cartera_plano.json"
PASS_FILE = REPO / "cartera_pass.txt"
SALIDA = REPO / "docs" / "cartera.enc.json"

ITERACIONES = 310_000

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    print("Falta la libreria de cifrado. Instalala con:")
    print("    pip install cryptography")
    sys.exit(1)


def b64(b):
    return base64.b64encode(b).decode("ascii")


def main():
    if not PLANO.exists():
        print("ERROR: no encuentro %s" % PLANO)
        return 1

    datos = io.open(PLANO, encoding="utf-8-sig").read()
    try:
        json.loads(datos)
    except Exception as e:
        print("ERROR: cartera_plano.json no es JSON valido: %s" % e)
        return 1

    SENTINELAS = {"BORRAR-ESTE-ARCHIVO", "CAMBIAME", "clave-de-prueba-larga-2026"}
    clave, origen = None, None
    if PASS_FILE.exists():
        candidata = io.open(PASS_FILE, encoding="utf-8-sig").read().strip()
        if candidata in SENTINELAS:
            print("Aviso: cartera_pass.txt tiene un valor de prueba. Lo ignoro y te la pido.")
            print("       Borra ese archivo, o escribe dentro tu clave real.")
        elif candidata:
            clave, origen = candidata, "cartera_pass.txt"
    if clave is None:
        clave = getpass.getpass("Contrasena para la cartera: ")
        confirma = getpass.getpass("Repitela: ")
        if clave != confirma:
            print("ERROR: no coinciden.")
            return 1
        origen = "teclado"

    if len(clave) < 12:
        print("ERROR: usa al menos 12 caracteres.")
        print("       El archivo cifrado queda en un repo publico: con una")
        print("       contrasena corta, alguien podria probarla por fuerza bruta.")
        return 1

    salt = os.urandom(16)
    iv = os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), salt, ITERACIONES, 32)
    ct = AESGCM(key).encrypt(iv, datos.encode("utf-8"), None)

    sobre = {
        "v": 1,
        "kdf": "PBKDF2-SHA256",
        "iter": ITERACIONES,
        "salt": b64(salt),
        "iv": b64(iv),
        "ct": b64(ct),
    }
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    with open(SALIDA, "wb") as f:
        f.write(json.dumps(sobre).encode("utf-8"))

    print("OK. Cifrada con la contrasena tomada de: %s" % origen)
    print("    entrada : %s (%d bytes en claro)" % (PLANO.name, len(datos)))
    print("    salida  : docs/%s (%d bytes cifrados)" % (SALIDA.name, SALIDA.stat().st_size))
    print("")
    print("El Publisher la sube sola en su proxima pasada.")
    print("Abrila en: https://jaredbellov.github.io/value-signal-system/cartera.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
