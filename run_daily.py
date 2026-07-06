"""
Daily fantasy baseball assistant.

Runs the full pipeline:
  1. Pulls fresh data from Yahoo, FanGraphs, FantasyPros, MLB
  2. Generates start/sit recommendations
  3. Analyzes roster for pickup opportunities
  4. Saves a readable report to data/{date}/report.txt

Can be run manually or scheduled via Windows Task Scheduler.
"""
import sys
import os
from datetime import date
from io import StringIO

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()


def check_cookie():
    """Check if FantasyPros cookie is set, prompt if missing."""
    cookie = os.getenv('FANTASYPROS_COOKIE', '')
    if not cookie:
        print("\n" + "=" * 60)
        print("  FantasyPros cookie is not set!")
        print("  To get it:")
        print("  1. Log into fantasypros.com in your browser")
        print("  2. Open DevTools (F12) > Console")
        print("  3. Type: document.cookie")
        print("  4. Copy the full output")
        print("=" * 60)
        new_cookie = input("\nPaste your cookie here (or press Enter to skip): ").strip()
        if new_cookie:
            # Update .env file
            env_path = os.path.join(os.path.dirname(__file__), '.env')
            with open(env_path, 'r') as f:
                lines = f.readlines()

            updated = False
            with open(env_path, 'w') as f:
                for line in lines:
                    if line.startswith('FANTASYPROS_COOKIE='):
                        f.write(f'FANTASYPROS_COOKIE={new_cookie}\n')
                        updated = True
                    else:
                        f.write(line)
                if not updated:
                    f.write(f'FANTASYPROS_COOKIE={new_cookie}\n')

            os.environ['FANTASYPROS_COOKIE'] = new_cookie
            print("  Cookie saved to .env!")
        else:
            print("  Skipping FantasyPros daily projections (will only get 10 rows).")


def run():
    today = date.today().isoformat()
    data_dir = os.path.join('data', today)
    report_path = os.path.join(data_dir, 'report.txt')

    print(f"\n{'='*60}")
    print(f"  FANTASY BASEBALL ASSISTANT - {today}")
    print(f"{'='*60}\n")

    # Check cookie before starting
    check_cookie()

    # Step 1: Pull data
    print("\n" + "=" * 60)
    print("  STEP 1: PULLING FRESH DATA")
    print("=" * 60 + "\n")

    try:
        from daily_pull import pull_daily_data
        pull_daily_data()
    except RuntimeError as e:
        if 'cookie' in str(e).lower():
            print(f"\n  ERROR: {e}")
            print("  Prompting for new cookie...\n")
            check_cookie()
            # Retry
            from importlib import reload
            import src.fantasypros_scraper
            reload(src.fantasypros_scraper)
            pull_daily_data()
        else:
            raise

    # Step 2: Start/Sit recommendations
    print("\n" + "=" * 60)
    print("  STEP 2: START/SIT RECOMMENDATIONS")
    print("=" * 60)

    from src.recommender import generate_recommendations, print_recommendations
    recs = generate_recommendations(data_dir)
    print_recommendations(recs)

    # Step 3: Pickup analysis
    print("\n" + "=" * 60)
    print("  STEP 3: PICKUP ANALYSIS")
    print("=" * 60)

    from src.pickup_analyzer import analyze_pickups, print_analysis
    pickups = analyze_pickups(data_dir)
    print_analysis(pickups)

    # Save report to file
    # Re-run the print functions capturing output
    os.makedirs(data_dir, exist_ok=True)
    buf = StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf

    print(f"FANTASY BASEBALL DAILY REPORT - {today}")
    print(f"{'='*60}\n")
    print_recommendations(recs)
    print()
    print_analysis(pickups)

    sys.stdout = old_stdout
    report = buf.getvalue()

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"\n  Report saved to: {report_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    try:
        run()
    except Exception as e:
        print(f"\n  ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        input("\n  Press Enter to close...")
