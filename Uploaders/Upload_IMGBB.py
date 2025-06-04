import json
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.options import Options  # Import ChromeOptions
from loguru import logger
import time


async def load_json_file(file_name):
    try:
        with open(file_name, 'r') as config_file:
            json_data = json.load(config_file)
            return json_data, 0  # Success
    except FileNotFoundError:
        logger.error(f"{file_name} file not found.")
        return None, -1  # JSON file load error
    except KeyError as e:
        logger.error(f"Key {e} is missing in the {file_name} file.")
        return None, -2  # Missing keys in JSON
    except json.JSONDecodeError:
        logger.error(f"Error parsing {file_name}. Ensure the JSON is formatted correctly.")
        return None, -3  # JSON file load error
    except Exception:
        logger.exception(f"An unexpected error occurred while loading {file_name}.")
        return None, -4  # Unknown exception


async def change_suffix(url: str, new_suffix: str) -> str:
    fixed_url = str(url.rsplit('.', 1)[0] + '.' + new_suffix.lstrip('.'))
    return fixed_url


async def upload_to_imgbb(headless_mode, imgbb_username, imgbb_password, imgbb_album_id, filepath, new_filename_base_name, image_output_format, mode):
    """Logs in to ImgBB, navigates to homepage, uploads an image, and saves the direct link to a timestamped text file in the working path."""

    try:
        # Configure ChromeOptions for headless mode
        chrome_options = Options()
        if headless_mode:
            chrome_options.add_argument("--headless=new")  # Use the new headless mode
        else:
            chrome_options = None

        # Initialize the WebDriver with the headless options
        driver = webdriver.Chrome(options=chrome_options)  # Or webdriver.Firefox(), etc.

        # Navigate to the ImgBB login page
        driver.get("https://imgbb.com/login")

        # Wait for the email and password fields to be present and fill them dynamically
        email_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "login-subject"))
        )
        password_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "password"))
        )
        email_field.send_keys(imgbb_username)
        password_field.send_keys(imgbb_password)

        # Submit the login form using a more specific XPath for the button
        login_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//form[@data-action='validate']//button[@type='submit' and contains(text(), 'Sign in')]"))
        )
        login_button.click()

        # Wait for the login to complete (you might need to adjust the wait condition)
        WebDriverWait(driver, 10).until(EC.url_to_be(f"https://{imgbb_username}.imgbb.com/"))

        # Navigate to the homepage
        driver.get("https://imgbb.com/")

        # Wait for the "Start uploading" button to be clickable
        upload_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//div[@id='home-cover-content']//a[@data-trigger='anywhere-upload-input' and contains(text(), 'Start uploading')]"))
        )

        # Wait for the file input field to be present and send the image path
        file_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))
        )
        file_input.send_keys(str(filepath))  # Send the string representation of the Path object
        time.sleep(0.5)  # Keeping the sleep might be helpful for the page to process

        # Wait for the album select dropdown to be visible
        album_select = WebDriverWait(driver, 20).until(
            EC.visibility_of_element_located((By.ID, "upload-album-id"))
        )

        # Use the Select class to interact with the dropdown
        album_dropdown = Select(album_select)
        album_dropdown.select_by_value(imgbb_album_id)

        # Wait for the "Upload" button to be clickable and click it
        upload_submit_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//div[@id='anywhere-upload-submit']//button[@data-action='upload' and contains(text(), 'Upload')]"))
        )
        upload_submit_button.click()

        # Wait for the upload to complete and the embed codes section to be visible
        embed_codes_div = WebDriverWait(driver, 60).until(
            EC.visibility_of_element_located((By.CLASS_NAME, "copy-hover-display"))
        )

        # Locate the embed codes dropdown
        embed_dropdown_element = WebDriverWait(embed_codes_div, 10).until(
            EC.presence_of_element_located((By.ID, "uploaded-embed-toggle"))
        )
        embed_dropdown = Select(embed_dropdown_element)

        # Get Direct Link:
        # Select "Direct links" from the dropdown
        embed_dropdown.select_by_visible_text("Direct links")

        # Wait for the direct link textarea to become visible and get its value
        direct_link_textarea = WebDriverWait(embed_codes_div, 10).until(
            EC.visibility_of_element_located((By.XPATH, ".//div[@data-combo-value='direct-links']//textarea[@name='direct-links']"))
        )
        direct_link_text = direct_link_textarea.get_attribute("value")
        # Get Viewer Link:
        # Select "Viewer links" from the dropdown
        embed_dropdown.select_by_visible_text("Viewer links")

        # Wait for the direct link textarea to become visible and get its value
        viewer_link_textarea = WebDriverWait(embed_codes_div, 10).until(
            EC.visibility_of_element_located((By.XPATH, ".//div[@data-combo-value='viewer-links']//textarea[@name='viewer-links']"))
        )
        viewer_link_text = viewer_link_textarea.get_attribute("value")
        fixed_direct_link_url = await change_suffix(direct_link_text, image_output_format)

        result = {
            "direct_link": fixed_direct_link_url,
            "viewer_link": viewer_link_text
        }

        key = f"{new_filename_base_name} - {mode}"
        txt_filename = f"{new_filename_base_name}_imgbb.txt"
        txt_filepath = os.path.join(os.path.dirname(filepath), txt_filename)

        if os.path.exists(txt_filepath):
            with open(txt_filepath, "r+", encoding="utf-8") as f:
                contents = f.read()
                try:
                    data = json.loads(contents)  # Try to load existing JSON data
                except json.JSONDecodeError:
                    data = {}  # If the file is not valid JSON, create a new dictionary

                # Add the result under the specific key
                if key not in data:
                    data[key] = []
                data[key].append(result)

                # Write the updated contents back to the file
                f.seek(0)
                f.write(json.dumps(data, indent=2))
                f.truncate()  # Ensure no leftover data
                # logger.debug(f"Appended new upload result under key '{key}' in existing file: {txt_filepath}")

        else:
            # If the file doesn't exist, create a new one and add the key with result
            data = {key: [result]}
            with open(txt_filepath, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2))
                # logger.debug(f"Created new result file with key '{key}': {txt_filepath}")

        driver.quit()
        return result

    except Exception as e:
        logger.error(f"Exception during upload: {e}")
        if 'driver' in locals() and driver is not None:
            driver.quit()
        return False


async def imgbb_upload_single_image(filepath, new_filename_base_name, headless_mode, image_output_format, mode):

    # Load Config file
    creds, exit_code = await load_json_file("../creds.secret")
    if not creds:
        exit(exit_code)
    imgbb_username = creds["imgbb_username"]
    imgbb_password = creds["imgbb_password"]
    imgbb_album_id = creds["imgbb_album_id"]

    result = await upload_to_imgbb(headless_mode, imgbb_username, imgbb_password, imgbb_album_id, filepath, new_filename_base_name, image_output_format, mode)
    if result:
        logger.success(f"Upload has been completed for image: {filepath}")
        return True
    else:
        logger.error(f"Upload failed for image: {filepath}")
        return False


# Example Usage
# if __name__ == "__main__":
#     # Add Logger to file
#     logger.add("App_Log_{time:YYYY.MMMM}.log", rotation="30 days", backtrace=True, enqueue=False, catch=True)  # Load Logger
#     upload_single_image()
