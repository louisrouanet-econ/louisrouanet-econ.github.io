# Louis Rouanet's website

Preview: https://louisrouanet-econ.github.io/

This is a static version of the public website exported from WordPress on October 17, 2026. It is hosted with GitHub Pages and can be edited with ChatGPT/Codex, Claude, a text editor, or GitHub's web editor.

## Editing

- Edit `index.html` to change the biography, research, teaching, contact links, or styling. Each publication is a separate `<article>`.
- PDF files and photographs live under `wp-content/uploads/`. The folder name preserves existing download URLs; WordPress is not required.
- Upload a new PDF, then update its link in `index.html`. Keep older files when external pages may link to them.
- Commit to `main`. The **Publish academic website** workflow checks local links and publishes automatically. Wait for the green deployment result before checking the live site.
- To work locally, run `python -m http.server 8000` from this folder and open http://localhost:8000/.
- To download the entire static website, use GitHub **Code → Download ZIP**.

## Migration status

- All 27 media attachments recorded in the supplied WordPress export have been copied into this repository. `asset-import.json` records source addresses, sizes, and SHA-256 checksums.
- All 20 research entries from the original home page are retained. The teaching section now lists ISS 2440 (Introduction to PPEL, UF), whose button links to the course's GitHub repository; the older GMU course entries were removed, but their syllabus PDFs remain under `wp-content/uploads/` so old links keep working.
- Two older syllabus URLs now serve the corresponding syllabus found in the export. The old `/2024/08/Rouanet_Tallec_Long_Live_Republic.pdf` address was already unavailable on WordPress; the current working paper uses the available November 2025 PDF.
- Coauthor website links are retained. The old contact form has been replaced with an email link because GitHub Pages does not run WordPress/PHP form handlers.
- The contact email still matches the original site: `lcrouanet@utep.edu`. Confirm this is the intended address before switching the domain.
- The preview has `noindex,follow` to avoid search indexing during migration.
- `louisrouanet.com` still serves the original WordPress site. No domain/DNS changes have been made.
- A complete UpdraftPlus database/files backup was created on WordPress on October 17, 2026 at 12:11 EDT. Keep backup ZIPs and the WordPress XML export private; do not commit them here.

## Connect the domain when ready

1. Sign into the domain/DNS provider and confirm it manages `louisrouanet.com`.
2. Verify domain ownership in GitHub and configure the custom domain in this repository's **Settings → Pages**.
3. Update only the necessary web DNS records, preserving email/MX and other unrelated records. Follow the current GitHub Pages custom-domain documentation: https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site.
4. Once DNS and the HTTPS certificate are ready, check the main domain, `www`, the CV, and the paper downloads. Enable **Enforce HTTPS**.
5. Remove the preview `noindex` meta tag from `index.html`, add the canonical URL `https://louisrouanet.com/`, and commit.
6. Keep the WordPress hosting and backup until the domain and downloads work reliably. Domain registration and any email service remain separate from GitHub hosting.

## Instructions for AI edits

Preserve publication titles, dates, journal names, coauthors, and PDF URLs unless explicitly asked to change them. Preserve the original media paths and the mobile layout. Never upload WordPress database dumps, backups, credentials, or the private XML export to this public repository.
