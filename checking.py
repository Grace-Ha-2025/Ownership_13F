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