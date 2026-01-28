import requests
import csv

API_URL = "https://content.sorghumbase.org/wordpress/index.php/wp-json/wp/v2/scientific_paper"
PER_PAGE = 100
TOTAL_PAGES = 17  # Update if more pages are added in the future
OUTPUT_CSV = "sorghumbase_papers.csv"

def extract_field(item, field):
    return item.get(field, "")

def main():
    all_rows = []
    for page in range(1, TOTAL_PAGES + 1):
        url = f"{API_URL}?per_page={PER_PAGE}&page={page}"
        resp = requests.get(url)
        resp.raise_for_status()
        papers = resp.json()
        for paper in papers:
            doi = extract_field(paper, "doi")
            pubmed_id = extract_field(paper, "pubmed_id")
            title = extract_field(paper, "title").get("rendered", "") if isinstance(paper.get("title"), dict) else paper.get("title", "")
            abstract = extract_field(paper, "abstract")
            authors = extract_field(paper, "authors")
            journal = extract_field(paper, "journal")
            year = extract_field(paper, "year")
            all_rows.append({
                "doi": doi,
                "pubmed_id": pubmed_id,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "journal": journal,
                "year": year
            })
        print(f"Fetched page {page}/{TOTAL_PAGES}")

    with open(OUTPUT_CSV, "w", newline='', encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["doi", "pubmed_id", "title", "abstract", "authors", "journal", "year"])
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Saved {len(all_rows)} records to {OUTPUT_CSV}")

if __name__ == "__main__":
    main()
