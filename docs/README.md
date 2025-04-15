# File Prepare HF

**File Prepare HF** is a Python-based tool designed to process and prepare media files for integration with Hugging Face datasets. It offers functionalities for media processing, preview generation, and interaction with ThePornDB (TPDB) API to enrich metadata.

## Features

- **Media Processing**: Handles various media files, extracting and preparing them for dataset integration.
- **Preview Generation**: Creates previews of media files based on configurable settings.
- **TPDB API Integration**: Fetches and processes metadata from ThePornDB API to enhance media information.
- **Configurable Settings**: Utilizes JSON configuration files to customize processing parameters.
- **Utility Functions**: Includes a set of utility functions to support media processing tasks.

## Installation

1. **Clone the repository**:

   ```bash
   git clone https://github.com/edstagdh/File_Prepare_HF.git
   cd File_Prepare_HF
   ```

2. **Create a virtual environment (optional but recommended)**:

   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install the required dependencies**:

   ```bash
   pip install -r Requirements.txt
   ```

## Usage

1. **Configure Settings**:

   - Rename `Config.json_example` to `Config.json` and adjust the settings as needed.
   - Rename `creds.secret_example` to `creds.secret` and input your TPDB API credentials.

2. **Run the Main Script**:

   ```bash
   python main.py
   ```

   This will initiate the media processing workflow based on your configurations.

## Configuration

- **`Config.json`**: Contains settings for media processing, such as input/output directories, preview options, and other parameters.
- **`creds.secret`**: Stores sensitive information like TPDB API keys. Ensure this file is kept secure and is not shared publicly.

## File Structure

```
File_Prepare_HF/
├── Config.json_example         # Example configuration file
├── creds.secret_example        # Example credentials file
├── Media_Processing.py         # Handles media file processing
├── Preview_Tool.py             # Generates media previews
├── TPDB_API_Processing.py      # Interacts with TPDB API
├── Utilities.py                # Utility functions for processing
├── main.py                     # Entry point of the application
├── Requirements.txt            # Python dependencies
├── docs/                       # Documentation files
└── .gitignore                  # Specifies files to ignore in Git
```

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.