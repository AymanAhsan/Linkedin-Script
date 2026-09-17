import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import link_builder

config = {
    "target_companies": ["Stripe", "Datadog", "Ramp"],
    "titles": ["Software Engineer", "SWE Intern"],
}

def build_url_test():
    expected_url = "https://www.linkedin.com/search/results/people/?keywords=Software%20Engineer%20Stripe%20OR%20SWE%20Intern%20Stripe%20OR%20Software%20Engineer%20Datadog%20OR%20SWE%20Intern%20Datadog%20OR%20Software%20Engineer%20Ramp%20OR%20SWE%20Intern%20Ramp"
    generated_url = link_builder(config["target_companies"], config["titles"])
    assert generated_url == expected_url, f"Expected: {expected_url}, but got: {generated_url}"

def build_url_with_connection_test():
    expected_url = "https://www.linkedin.com/search/results/people/?keywords=Software%20Engineer%20Stripe%20OR%20SWE%20Intern%20Stripe%20OR%20Software%20Engineer%20Datadog%20OR%20SWE%20Intern%20Datadog%20OR%20Software%20Engineer%20Ramp%20OR%20SWE%20Intern%20Ramp&connectionOf=me"
    generated_url = link_builder(config["target_companies"], config["titles"], connected=True)
    assert generated_url == expected_url, f"Expected: {expected_url}, but got: {generated_url}"


if __name__ == "__main__":
    build_url_test()
    print("PASSED")


