import pandas as pd
import numpy as np

def calculate_player_values(projections_df):
    """
    Calculate player values based on custom 6x6 categories using Z-scores.

    Hitters: R, HR, RBI, SB, OBP, TB
    Pitchers: W, K, ERA, WHIP, QS, SV+H

    Args:
        projections_df (pd.DataFrame): DataFrame with player projections

    Returns:
        pd.DataFrame: DataFrame with added total_value column, sorted by value
    """
    # Separate hitters and pitchers
    # Assuming there's a 'position' column or similar to distinguish
    # For simplicity, assume hitters are those with HR, RBI, etc., pitchers with W, K, etc.
    # You may need to adjust based on actual data structure

    # Define categories
    hitter_categories = ['R', 'HR', 'RBI', 'SB', 'OBP', 'TB']
    pitcher_categories = ['W', 'K', 'ERA', 'WHIP', 'QS', 'SV_H']  # Assuming SV+H is 'SV_H'

    # Identify hitters and pitchers
    # This is a placeholder; adjust based on your data
    hitters = projections_df[projections_df['position'].isin(['C', '1B', '2B', '3B', 'SS', 'OF', 'DH'])]
    pitchers = projections_df[projections_df['position'].isin(['SP', 'RP'])]

    # Function to calculate Z-scores and total value
    def calculate_values(df, categories, is_hitter=True):
        # For ERA and WHIP, lower is better, so negate Z-score
        negative_stats = ['ERA', 'WHIP'] if not is_hitter else []

        z_scores = []
        for cat in categories:
            if cat in negative_stats:
                # For negative stats, higher projections are worse, so Z-score is negative
                mean = df[cat].mean()
                std = df[cat].std()
                z = -(df[cat] - mean) / std  # Negative because lower is better
            else:
                z = (df[cat] - df[cat].mean()) / df[cat].std()
            z_scores.append(z)

        # Sum Z-scores
        df = df.copy()
        df['total_value'] = sum(z_scores)
        return df

    # Calculate for hitters
    hitters_valued = calculate_values(hitters, hitter_categories, is_hitter=True)

    # Calculate for pitchers
    pitchers_valued = calculate_values(pitchers, pitcher_categories, is_hitter=False)

    # Merge back
    valued_df = pd.concat([hitters_valued, pitchers_valued])

    # Sort by total_value descending
    valued_df = valued_df.sort_values('total_value', ascending=False)

    return valued_df