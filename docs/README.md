# File Prepare HF

**File Prepare HF** is a Python-based tool designed to process and prepare media files for uploading to HF. It offers functionalities for media processing, preview generation, and interaction with ThePornDB (TPDB) API to enrich metadata.
<br><br>
This process requires matching scene via TPDB, it is not a standalone script.

## Features

- **Media Processing**: Includes re-encoding of videos and creation of cover image and thumbnails, all set via flags in Config.json
- **Preview Generation**: Creates previews of videos based on configurable settings.
- **TPDB API Integration**: Fetches and processes metadata from ThePornDB API.
- **Configurable Settings**: Utilizes JSON configuration files to customize processing parameters.
- **Utility Functions**: Includes a set of utility functions to support media processing and general tasks.
- **Template Generation**: Includes option to generate upload BBCode for each video based on existing template file, including tags/mediainfo.


## 🛣️ Roadmap

- [x] Add tags generation for scene upload process.
- [ ] Add an option to process files without fetching data from TPDB API(Re-encode, Create Previews).
- [ ] Support for Static thumbnails generation without using Scorp VTM software.
- [ ] Support other types of databases, e.g. StashDB.


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

0. **Prerequisites**:

   - Python version 3.10(any sub versions should work) for maximum compatibility.
   - TPDB credentials(API Key).
   - MP4 extension.
   - Valid HF template file.
   - FFMPEG installed in configured via PATH(recent ffmpeg version)
   - Valid filename format, see example below(use only female performer names),
   Note - this still might not be enough for a match in TPDB.
   ```
   STUDIO_NAME.YY.MM.DD.PERFORMER_FNAME.PERFORMER_LNAME.EXTENSION
   or
   STUDIO_NAME.YY.MM.DD.PERFORMER_FNAME.PERFORMER_LNAME.PART.NUMBER.EXTENSION
   or
   STUDIO_NAME.YY.MM.DD.PERFORMER1_FNAME.PERFORMER1_LNAME.and.PERFORMER2_FNAME.PERFORMER2_LNAME.EXTENSION
   ```
<br>
1. **Configure Settings**:

   - Rename `Config.json_example` to `Config.json` and adjust the settings as needed.
   - Rename `creds.secret_example` to `creds.secret` and input your TPDB API credentials.
   - If you intend to you Scrop VTM(Video Thumbnails Maker), Configure the settings as you like in the VTM with the following settings being a prerequisite:
     - Set output format file to ".jpg" file
     - add suffix "_thumbnails"
     - "Remove video extension" from output file name, example output format:
     ```"Studio.YY.MM.DD.FName.LName_thumbnails.jpg"```
     
   If you would like a template for VTM, you can find it here: Resources/edstagdh.vtm
   <br>This does not replace configuring the required settings for output file name/format.  
<br>
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
├── BBCode_Images.json          # Icons mapping for images URLs
├── Config.json_example         # Example configuration file
├── creds.secret_example        # Example credentials file
├── HF_Template.txt             # Example template file with placeholders for HF uploading
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