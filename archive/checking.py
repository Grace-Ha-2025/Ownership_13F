# ARCHIVED: one-off QA check, not part of any pipeline step. Verifies the
# arithmetic in a stock_sentiment CSV (buyers+sellers+unchanged == n_managers)
# produced by the old all_manager_diff.py run. Kept for reference only.
import pandas as pd

# Load the file
df = pd.read_csv("stock_sentiment_2025q4_to_2026q1.csv")

# Verify that buyers + sellers + unchanged equals n_managers across all rows
df["calc_total"] = df["buyers"] + df["sellers"] + df["unchanged"]
discrepancies = df[df["calc_total"] != df["n_managers"]]

if discrepancies.empty:
    print("All row manager counts reconcile successfully!")
else:
    print(f"Found {len(discrepancies)} rows with mismatched manager totals.")