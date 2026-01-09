import asyncio
import json
import os
from loguru import logger
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException, StaleElementReferenceException
from Utilities import load_credentials, load_json_file
from Generate_Torrent_File import generate_torrent_process
from pathlib import Path


async def delete_prefixed_files(base_path: str, suffixes: list[str], prefix: str = "") -> None:
    """
    Delete all files in base_path that match prefix + suffix from the list.
    """
    try:
        for suffix in suffixes:
            file_path = Path(base_path) / f"{prefix}{suffix}"
            if file_path.exists():
                try:
                    file_path.unlink()
                    logger.info(f"Deleted file: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete {file_path}: {e}")
    except Exception:
        logger.exception("Error in delete_prefixed_files")


async def get_user_input_form_submit_status():
    """
    Asks the user for a yes/no response.
    If 'yes', returns True.
    If 'no', continues asking for user input until a status response is given.
    If 'cancel', returns False.
    Continues prompting until a valid response is given.
    """
    while True:
        try:
            response = input("Did you finish submitting the form? (yes[y]/no[n]/cancel[c]): \n").strip().lower()
            if response in ["yes", "y"]:
                return True
            elif response in ["cancel", "c"]:
                return False
            elif response in ["no", "n"]:
                logger.info("Please continue with the form, once done, input the correct response required, 'yes' or 'y'.\nif you would like to cancel the process, input 'cancel'")
            else:
                logger.warning("Invalid input. Please enter 'yes' or 'no'.\n")
        except Exception as e:
            logger.error(f"An error occurred: {e}")
            return False


async def get_user_input_2fa():
    """
    Asks the user for a yes/no response.
    If 'yes', returns True.
    If 'no', continues asking for user input until a status response is given.
    If 'cancel', returns False.
    Continues prompting until a valid response is given.
    """
    while True:
        try:
            response = input("Did you finish login-in process with 2 factor authentication? (yes[y]/no[n]/cancel[c]): \n").strip().lower()
            if response in ["yes", "y"]:
                return True
            elif response in ["cancel", "c"]:
                return False
            elif response in ["no", "n"]:
                logger.info("Please continue with the 2 factor authentication process, once done, input the correct response required, 'yes' or 'y'.\nif you would like to cancel the process, input 'cancel'")
            else:
                logger.warning("Invalid input. Please enter 'yes' or 'no'.\n")
        except Exception as e:
            logger.error(f"An error occurred: {e}")
            return False

async def get_by(selector_obj):
    by_map = {
        "id": By.ID,
        "name": By.NAME,
        "css": By.CSS_SELECTOR,
        "xpath": By.XPATH,
        "class": By.CLASS_NAME,
        "tag": By.TAG_NAME,
        "link": By.LINK_TEXT,
    }
    if "by" not in selector_obj or "value" not in selector_obj:
        raise ValueError(f"Invalid selector object: {selector_obj}")
    by_key = selector_obj["by"].lower()
    value = selector_obj["value"]
    if by_key not in by_map:
        raise ValueError(f"Unsupported selector: {by_key}")
    return by_map[by_key], value


async def init_browser(config):
    try:
        browser_type = config.get("browser", "").lower()
        if not browser_type:
            raise ValueError("Browser type not specified in config.")

        if browser_type == "firefox":
            from selenium.webdriver.firefox.options import Options as FirefoxOptions

            logger.info("Launching Firefox...")
            driver_path = config.get("firefox_driver_path")
            if not driver_path or not os.path.exists(driver_path):
                raise ValueError(f"Invalid or missing Firefox driver path: {driver_path}")

            options = FirefoxOptions()
            options.set_preference("dom.webdriver.enabled", False)
            options.set_preference("useAutomationExtension", False)
            options.add_argument("--private")
            options.add_argument("--no-remote")

            profile_path = config.get("firefox_profile_path")
            if profile_path:
                if not os.path.exists(profile_path):
                    raise ValueError(f"Invalid Firefox profile path: {profile_path}")
                options.add_argument("-profile")
                options.add_argument(profile_path)

            service = FirefoxService(executable_path=driver_path)
            driver = webdriver.Firefox(service=service, options=options)

        elif browser_type == "chrome":
            from selenium.webdriver.chrome.options import Options as ChromeOptions

            logger.info("Launching Chrome...")
            driver_path = config.get("chrome_driver_path")
            if not driver_path or not os.path.exists(driver_path):
                raise ValueError(f"Invalid or missing Chrome driver path: {driver_path}")

            options = ChromeOptions()
            options.add_argument("--incognito")
            options.add_argument("--disable-blink-features=AutomationControlled")

            profile_path = config.get("chrome_profile_path")
            if profile_path:
                if not os.path.exists(profile_path):
                    raise ValueError(f"Invalid Chrome profile path: {profile_path}")
                options.add_argument(f"user-data-dir={profile_path}")

            service = ChromeService(executable_path=driver_path)
            driver = webdriver.Chrome(service=service, options=options)

        else:
            raise ValueError(f"Unsupported browser: {browser_type}")

        driver.set_window_size(1280, 800)
        wait = WebDriverWait(driver, 15)
        return driver, wait

    except WebDriverException as e:
        logger.exception("Failed to initialize browser.")
        raise


async def process_upload_to_tracker(tracker_mode, new_filename_base_name, output_dir, template_file_full_path, new_title, hamster_file_path, save_path, remove_e_files,
                                    resolution, codec, is_last_tracker):
    driver = None
    try:
        # Load Config_Tracker.json
        config, code = await load_json_file("Configs/Config_Tracker.json")
        if config is None or code != 0:
            logger.error("Failed to load configuration.")
            return False

        # Load credentials
        trackers, _, _ = await load_credentials(6)
        username = trackers[f"{tracker_mode}_tracker_u"]
        password = trackers[f"{tracker_mode}_tracker_p"]
        p_ann_url = trackers[f"{tracker_mode}_tracker_ann_url"]
        if not username or not password or not p_ann_url:
            logger.error("Credentials missing or invalid.")
            return False

        # Additional information
        _, template_name = os.path.split(template_file_full_path)
        template_base_name, _ = os.path.splitext(template_name)
        # Load the tags content
        tags_filename = os.path.join(output_dir, f"{new_filename_base_name}_tags.txt")
        with open(tags_filename, "r", encoding="utf-8") as f:
            tags_content = f.read()
        template_filename = os.path.join(output_dir, f"{new_filename_base_name}_template.txt")
        with open(template_filename, "r", encoding="utf-8") as f:
            template_content = f.read()

        if hamster_file_path != "" and os.path.isfile(hamster_file_path):
            try:
                with open(hamster_file_path, "r", encoding="utf-8") as f:
                    hamster_data = json.load(f)

                preview_key = f"{new_filename_base_name} - Preview WebP"

                if preview_key in hamster_data and isinstance(hamster_data[preview_key], list):
                    preview_entry = hamster_data[preview_key][0]
                    if "image_url" in preview_entry:
                        preview_image_url = preview_entry["image_url"]
                    else:
                        logger.error(f"no preview image url detected in {hamster_data}")
                        raise

            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
                raise ValueError(f"Failed to parse hamster file or missing expected data: {e}")
        else:
            preview_image_url = None

        # Initialize browser
        driver, wait = await init_browser(config)

        # --- Login ---
        login_url = config["trackers_urls"][f"{tracker_mode}_tracker_upload_url"]
        driver.get(login_url)
        logger.info(f"Opened login page: {login_url}")

        login_selectors = config["login_selectors"]

        # Username
        try:
            by_user, val_user = await get_by(login_selectors["username"])
            wait.until(EC.presence_of_element_located((by_user, val_user)))
            driver.find_element(by_user, val_user).send_keys(username)
        except Exception as e:
            logger.error(f"Failed to fill username: {e}")
            return False

        # Password
        try:
            by_pass, val_pass = await get_by(login_selectors["password"])
            driver.find_element(by_pass, val_pass).send_keys(password)
        except Exception as e:
            logger.error(f"Failed to fill password: {e}")
            return False

        # Submit
        try:
            by_submit, val_submit = await get_by(login_selectors["submit"])
            driver.find_element(by_submit, val_submit).click()
            logger.info("Login submitted.")
        except Exception as e:
            logger.error(f"Failed to click login submit: {e}")
            return False

        # 2FA Auth
        two_factor_auth = config.get("2FA", None)
        try:
            if two_factor_auth:
                await asyncio.sleep(0.2)
                two_factor_auth_result = await get_user_input_2fa()
                if not two_factor_auth_result:
                    raise ValueError("2FA failed")
                await asyncio.sleep(0.2)
        except Exception:
            logger.exception("Error during 2 factor authentication process")
            return False


        # Wait for login success
        login_success_selector = config["login_success"]
        by_success, val_success = await get_by(login_success_selector)
        try:
            wait.until(EC.presence_of_element_located((by_success, val_success)))
            logger.success("Login successful.")
        except TimeoutException:
            logger.error("Login failed or timeout.")
            return False

        # --- Navigate to form page ---
        form_url = config["trackers_urls"][f"{tracker_mode}_tracker_upload_url"]
        driver.get(form_url)
        logger.info(f"Navigated to form page: {form_url}")
        custom_codecs = ["av1", "hevc"]
        updated_title = f"{new_title} - {codec.upper()} - {resolution}" if codec in custom_codecs else f"{new_title} - {resolution}"

        # --- Fill form fields ---
        for field in config.get("form_fields", []):
            try:
                by_field, val_field = await get_by(field)
                wait.until(EC.presence_of_element_located((by_field, val_field)))
                if field["value"] == "category":
                    driver.find_element(by_field, val_field).send_keys("Pron")
                if field["value"] == "taginput":
                    driver.find_element(by_field, val_field).send_keys(tags_content)
                if field["value"] == "desc":
                    driver.find_element(by_field, val_field).send_keys(template_content)
                if field["value"] == "image":
                    driver.find_element(by_field, val_field).send_keys(preview_image_url)
                if field["value"] == "title":
                    driver.find_element(by_field, val_field).send_keys(updated_title)

            except Exception as e:
                logger.error(f"Failed to fill field {field}: {e}")
                return False

        # Create Torrent File
        list_suffixes_ignore = [
            "_preview.webp", "_preview.webm", "_preview.gif", "_preview_sheet.webp",
            "_preview_sheet.webm", "_preview_sheet.gif", "_hamster.txt", "_imgbb.txt",
            "_imgbox.txt", f"_template.txt", f"_tags.txt", "_mediainfo.txt"
        ]
        torrent_file = await generate_torrent_process(output_dir, save_path, new_filename_base_name, p_ann_url, list_suffixes_ignore)
        # Insert Torrent File
        try:
            if not torrent_file:
                raise
            by_torrent, val_torrent = await get_by(config["torrent_field"])
            driver.find_element(by_torrent, val_torrent).send_keys(torrent_file)
        except Exception as e:
            logger.error(f"Failed to insert torrent file: {e}")
            return False

        # --- Check for duplicates ---
        try:
            # Click the dupe check button
            by_dupe, val_dupe = By.NAME, "checkonly"
            dupe_btn = wait.until(EC.element_to_be_clickable((by_dupe, val_dupe)))
            dupe_btn.click()
            logger.info("Clicked 'Check for dupes' button.")

            # Wait for either possible message to appear
            def get_dupe_message(driver):
                try:
                    # Case 1: possible duplicates
                    mb = driver.find_element(By.ID, "messagebar")
                    if mb.is_displayed() and mb.text.strip():
                        return mb.text.strip()
                except StaleElementReferenceException:
                    return False
                except Exception:
                    pass

                try:
                    # Case 2: no duplicates
                    mb2 = driver.find_element(By.CSS_SELECTOR, "div.box.pad.shadow.center.rowa")
                    if mb2.is_displayed() and mb2.text.strip():
                        return mb2.text.strip()
                except StaleElementReferenceException:
                    return False
                except Exception:
                    return False

                return False

            def delete_torrent_file(torrent_file):
                file_path = Path(torrent_file)
                if file_path.exists():
                    try:
                        file_path.unlink()
                        logger.info(f"Deleted file: {file_path}")
                        return True
                    except Exception as e:
                        logger.warning(f"Failed to delete {file_path}: {e}")
                        return False

            message_text = WebDriverWait(driver, 60, poll_frequency=0.5).until(get_dupe_message)

            # Analyze message
            msg_lower = message_text.lower()
            if "possible dupes" in msg_lower:
                logger.warning(f"Duplicate detected: {message_text}")
                torrent_file_cleanup = delete_torrent_file(torrent_file)
                if not torrent_file_cleanup:
                    logger.warning(f"failed to delete existing torrent file: {torrent_file}")
            elif "no exact size dupes" in msg_lower:
                logger.success(f"No duplicates detected: {message_text}")
                torrent_file_cleanup = delete_torrent_file(torrent_file)
                if not torrent_file_cleanup:
                    logger.warning(f"failed to delete existing torrent file: {torrent_file}")
            else:
                logger.info(f"Dupe check completed: {message_text}")

        except TimeoutException:
            logger.warning("Dupe check message did not appear in time.")
        except Exception as e:
            logger.error(f"Failed during dupe check: {e}")

        await asyncio.sleep(0.2)

        """ --- Submit form ---
        # by_form_submit, val_form_submit = await get_by(config["form_submit"])
        # driver.find_element(by_form_submit, val_form_submit).click()
        """
        try:
            form_submit_result = await get_user_input_form_submit_status()

            if form_submit_result:
                # Cleanup files regardless of form result
                if remove_e_files and is_last_tracker:
                    await delete_prefixed_files(output_dir, list_suffixes_ignore, new_filename_base_name)
                logger.success("Form submitted successfully!")
                return True
            else:
                logger.error("Something went wrong during upload form submission process.")
                return False
        except Exception:
            logger.exception("Error during form submission and cleanup")
            return False

    except Exception as e:
        logger.error(f"Failed to fill the form: {e}")
        return False

    finally:
        if driver:
            driver.quit()
