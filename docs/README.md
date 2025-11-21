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
- [x] Support for Static thumbnails generation without using Scorp VTM software.
- [ ] ~~Support other types of databases, e.g. StashDB.~~ Note - I don't have access to other databases at this time.
- [x] Add an option to use jpg format images.
- [x] Add an option to upload to imgbox(supported static format images only), this requires restructure.
- [x] Add an option to upload to imgbb(webp)
- [x] Add an option to use free string parsing for scene matching.
- [x] Add an option to upload to hamster(webp)
- [ ] Add an option to change output file naming format(select from list)
- [x] Switch re-encoding progress bar from logger lines to tqdm.
- [x] support for cover image and thumbnails content regeneration flag:
<br>
See `Config.json` for `cover_regeneration_mode` and `Config_Thumbnails.json` for `regeneration_mode`
<br>
Available modes are [`user input`, `force regenerate`, `force keep`]
- [x] support for tracker semi-automatic uploading using selenium for python package, see `Config_Tracker.json`.
<br>
requires `create_template_file` to be set as True and currently only supported using hamster img host.
- [x] support for automatic torrent creation using torf package.
- [x] support for transitions for previews, see `Config_Video_Preview.json`




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
   - Valid HF template file<br>
   Note - Required only when using `create_hf_template` in the configuration.
   - FFMPEG installed in configured via PATH(recent ffmpeg version)
   - Valid filename format, see example below(use only female performer names)<br>
   Note - This still might not be enough for a match in TPDB.<br>
   Note 2 - Required only when not using `free_string_parse` in the configuration.
   ```
   STUDIO_NAME.YY.MM.DD.PERFORMER_FNAME.PERFORMER_LNAME.EXTENSION
   or
   STUDIO_NAME.YY.MM.DD.PERFORMER_FNAME.PERFORMER_LNAME.PART.NUMBER.EXTENSION
   or
   STUDIO_NAME.YY.MM.DD.PERFORMER1_FNAME.PERFORMER1_LNAME.and.PERFORMER2_FNAME.PERFORMER2_LNAME.EXTENSION
   ```

1. **Configure Settings**:

   - Rename `Config.json_example` to `Config.json` and adjust the settings as needed.
   - Rename `Config_Thumbnails.json_example` to `Config_Thumbnails.json` and adjust the settings as needed.
   - Rename `Config_Tracker.json_example` to `Config_Tracker.json` and adjust the settings as needed.
   - Rename `Config_Video_Preview.json_example` to `Config_Video_Preview.json` and adjust the settings as needed.
   - Rename `creds.secret_example` to `creds.secret` and input your TPDB API credentials and other credentials if needed.

2. **Run the Main Script**:

   ```bash
   python main.py
   ```

   This will initiate the media processing workflow based on your configurations.

## Configuration

- **`Config.json`**: Contains settings for media processing, such as input/output directories, preview options, and other parameters.
- **`Config_Thumbnails.json`**: Contains settings for thumbnails processing
- **`Config_Ttracker.json`**: Contains settings for tracker upload integration
- **`Config_Video_Preview.json`**: Contains settings preview processing
- **`creds.secret`**: Stores sensitive information like TPDB API keys. Ensure this file is kept secure and is not shared publicly.

## File Structure

```
File_Prepare_HF/
├── gitignore                                   # Specifies files to ignore in Git
└── Configs/                                    # Config files
   ├── Config.json_example                      # Example configuration file
   ├── Config_Thumbnails.json_example           # Example Thumbnails configuration file
   ├── Config_Tracker.json_example              # Example Tracker configuration file
   ├── Config_Video_Preview.json_example        # Example Preview configuration file
└── docs/                                       # Documentation files
   ├── exit_codes.json                          # Common exit codes for the application
   ├── README.md                                # README file
└── Image Uploaders/                            # Image Uploaders Integration
   ├── Upload_IMGBOX.py                         # IMGBOX code integration
   ├── Upload_IMGBB.py                          # IMGBB code integration
   ├── Upload_Hamster.py                        # Hamster code integration
└── Logs/                                       # Log files
└── Notifiers/                                  # Notifiers Integration
   ├── Notifier_TG.py                           # Telegram Notifier code integration
└── Resources/                                  # Resource files used in code
   ├── BBCode_Images.json                       # Icons mapping for images URLs
   ├── Gotham_Medium.otf                        # Font file for previews text overlay
   ├── HF_Template.txt                          # Example template file with placeholders for HF uploading
   ├── Performers_Images.json_Example           # Contains mapped performer face images to auto insert in template
   ├── Sort_Performers_Images.py                # Helper script to sort the performer images json.
├── creds.secret_example                        # Example credentials file
├── Generate_Thumbnails_Sheet.py                # Thumbnails generation code
├── Generate_Torrent_File.py                    # Torrent generation code
├── Generate_Video_Preview.py                   # Preview generation code
├── LICENSE                                     # License file
├── main.py                                     # Main Application code for processing files
├── Media_Processing.py                         # Handles media file processing
├── Requirements.txt                            # Python dependencies requirements file
├── TPDB_API_Processing.py                      # TPDB API code
├── Tracker_Uploader.py                         # Tracker Selenium Upload code integration
├── Utilities.py                                # Utility functions for processing

```

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.