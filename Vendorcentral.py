import pytest
import getpass
from playwright.sync_api import Page, expect


def test_vendor_central_image_upload(page: Page):
    """
    This script automates the login and image upload process for Amazon Vendor Central.
    It will prompt you in the terminal for your email, password, and OTP.
    """
    
    # 1. Get User Credentials from Terminal Input
    print("\nPlease provide your login credentials for Amazon Vendor Central.")
    email = input("Enter your email or mobile phone number: ")
    # Use getpass to securely input the password without showing it on screen
    password = getpass.getpass("Enter your password: ")

    # 2. Navigate and Login
    print("\nNavigating to Amazon Vendor Central...")
    page.goto('https://vendorcentral.amazon.com')

    print("Entering email...")
    page.get_by_role('textbox', name='Email or mobile phone number').fill(email)
    page.get_by_role('button', name='Continue').click()

    print("Entering password...")
    page.get_by_role('textbox', name='Password').fill(password)
    # Note: The original script had two sign-in clicks. This one is likely the main one.
    # The page.get_by_text('Sign in ..') is omitted as it might be redundant or brittle.
    page.get_by_role('button', name='Sign in').click()

    # 3. Handle One-Time Password (OTP)
    print("\nWaiting for the OTP page to load...")
    # Wait for the OTP input to be visible before asking the user for it
    otp_input = page.get_by_role('textbox', name='Enter OTP:')
    otp_input.wait_for(state="visible", timeout=60000) # Wait up to 60 seconds

    otp = input("Enter your OTP: ")
    
    print("Submitting OTP...")
    otp_input.fill(otp)
    page.get_by_role('button', name='Sign in').click()

    # 4. Select Vendor Account
    print("Selecting vendor account...")
    # Use a longer timeout here as the account selection page might take time to load
    page.wait_for_timeout(5000) # A static wait to allow for redirects, can be improved.
    
    # These selectors are specific to your account. You might need to adjust them.
    page.get_by_role('button', name='US - General Mills').click()
    page.get_by_role('button', name='Select account').click()

    # 5. Navigate to Image Upload Page
    print("Navigating directly to the image upload page...")
    page.goto('https://vendorcentral.amazon.com/imaging/upload')

    # 6. Upload the Image File
    print("Uploading the file...")
    page.get_by_test_id('bulk-upload-tab').click()
   
    # Playwright's set_input_files handles the file dialog. No need to click first.
    # Make sure the ZIP file is in the same directory as the script, or provide a full path.
    file_path = 'C:\\Users\\G713313\\OneDrive - General Mills\\Desktop\\Amazon-final\\B0DYFDHC7G.zip'
    print(f"Attaching file: {file_path}")
    # Locate the actual <input type="file"> element for file upload
    file_input = page.locator('input[type="file"]')  # Adjust the selector if necessary
    page.wait_for_timeout(10000)
    file_input.set_input_files(file_path)

    # 7. Finalize the Upload
    print("Completing the upload process...")
    # These are specific actions on your upload page
    page.locator('#katal-id-0').click()
    page.wait_for_timeout(10000)
    page.get_by_text('GENMI').click()
    page.wait_for_timeout(3000)
    # Wait for the upload preview button to be clicked
    page.get_by_test_id('upload-preview').get_by_role('button').click()
    page.wait_for_timeout(3000)

    # Ensure file upload completes
    print("Waiting for file upload confirmation...")
    try:
        upload_success = page.locator('div.upload-success')  # Adjust selector based on the page
        upload_success.wait_for(state="visible", timeout=10000)  # Wait up to 10 seconds
    except Exception as e:
        print("File upload confirmation not visible. Retrying upload...")
        page.wait_for_timeout(10000)  # Wait for 10 seconds before retrying
        print("Re-uploading the file...")
        file_input.set_input_files(file_path)  # Re-upload the file
        try:
            upload_success.wait_for(state="visible", timeout=10000)  # Wait again for confirmation
        except Exception as e:
            print("File upload failed again. Aborting...")
            return

    # Verify file input state
    uploaded_file = file_input.evaluate("el => el.files.length > 0")
    if not uploaded_file:
        print("File upload failed or was cleared. Aborting...")
        return

    # Click the Submit images button
    print("Clicking Submit images button...")
    submit_button = page.locator('kat-button[data-testid="file-upload-button"][data-name="submitUpload"]')
    submit_button.click()

    # Wait for the confirmation dialog to appear
    print("Waiting for confirmation dialog...")
    dialog_locator = page.locator('div.dialog')
    dialog_locator.wait_for(state="visible", timeout=10000)

    print("Confirmation dialog appeared. Ending script.")


if __name__ == "__main__":
    from playwright.sync_api import sync_playwright

    print("Starting Playwright...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            executable_path="C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
        )
        context = browser.new_context()
        page = context.new_page()

        try:
            test_vendor_central_image_upload(page)
        except Exception as e:
            print(f"An error occurred: {e}")
        finally:
            browser.close()

    print("Playwright session ended.")