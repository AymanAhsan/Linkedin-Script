
async def run(playwright):

    # Run from local browser settings
    user_data_dir = "C:\\Users\\Admin\\AppData\\Local\\Google\\Chrome\\User Data"
    browser = await playwright.chromium.launch(headless=True, user_data_dir=user_data_dir)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://google.com")
    print(await page.title())
    await browser.close()