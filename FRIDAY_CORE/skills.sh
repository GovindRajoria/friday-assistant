#!/bin/bash
# ==========================================
# FRIDAY ANTIGRAVITY BOOTSTRAP SCRIPT v2.0
# OS-Agnostic Setup with Automated Local LLM
# ==========================================

echo "[*] Initiating FRIDAY OS-Agnostic Setup..."

# 1. Detect Operating System
OS="$(uname -s)"
echo "[*] Detected OS: $OS"

case "$OS" in
    Linux*)
        echo "[+] Setting up Linux (Ubuntu/Debian) dependencies..."
        sudo apt-get update
        sudo apt-get install -y python3-venv python3-pip python3-dev \
            portaudio19-dev libasound-dev ffmpeg libsm6 libxext6 curl
        ;;
    Darwin*)
        echo "[+] Setting up macOS dependencies..."
        if ! command -v brew &> /dev/null; then
            echo "[-] Homebrew not found. Installing..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        fi
        brew install portaudio ffmpeg
        ;;
    CYGWIN*|MINGW32*|MSYS*|MINGW*)
        echo "[+] Setting up Windows dependencies..."
        echo "[-] Ensure C++ Build Tools and ffmpeg are in your PATH for optimal audio processing."
        ;;
    *)
        echo "[-] Unknown OS. Proceeding with caution..."
        ;;
esac

# 2. Automated Ollama Installation
echo "[*] Verifying Neural Engine (Ollama) status..."
if ! command -v ollama &> /dev/null; then
    echo "[-] Ollama not found. Initiating automated terminal installation..."
    
    case "$OS" in
        Linux*)
            curl -fsSL https://ollama.com/install.sh | sh
            ;;
        Darwin*)
            echo "[+] Downloading Ollama via Homebrew..."
            brew install --cask ollama
            ;;
        CYGWIN*|MINGW32*|MSYS*|MINGW*)
            echo "[+] Downloading Ollama via Windows Package Manager (winget)..."
            winget install Ollama.Ollama
            ;;
    esac
    
    echo "[+] Ollama installed successfully. Please ensure the Ollama application/service is running."
else
    echo "[+] Ollama is already installed."
fi

# 3. Model Initialization (Optimized for 8GB VRAM)
echo "[*] Pulling target routing model (Llama 3.1:8b)..."
# We run this in the background or allow it to download if missing
ollama pull llama3.1

# 4. Python Virtual Environment Setup
echo "[*] Bootstrapping isolated Python environment..."
python3 -m venv friday_env
source friday_env/bin/activate || source friday_env/Scripts/activate

# 5. Core Requirements Installation
echo "[*] Installing Core AI, Voice, and System Libraries..."
pip install --upgrade pip
pip install -r requirements.txt

# 6. Playwright Cross-Platform Browser Setup
echo "[*] Downloading headless browsers for web skills..."
playwright install --with-deps chromium

echo "[+] =========================================="
echo "[+] FRIDAY ANTIGRAVITY PROTOCOL FULLY ARMED."
echo "[+] Execute 'python -m core.main' to wake the system."
echo "[+] =========================================="
