from urllib.parse import quote

base_url = "https://www.linkedin.com/search/results/people/?keywords="

def link_builder(target_companies, titles, connected=False):
    """
    Builds a list of LinkedIn search URLs based on target companies and job titles.

    Args:
        target_companies (list): List of target company names.
        titles (list): List of job titles.
    Returns:
        str: A LinkedIn search URL.
    """

    url = base_url

    if target_companies and titles:
        # Create a search query for each combination of company and title
        search_queries = [f"{title} {company}" for company in target_companies for title in titles]
        # Join the queries with ' OR ' to create a single search string
        search_string = " OR ".join(search_queries)
        url += quote(search_string)

    if connected:
        url += "&connectionOf=me"
    return url