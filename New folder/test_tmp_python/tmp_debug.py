import traceback, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import run_case
try:
    run_case.run_single_case('LBWN', '2024-12-31', 18, 12, 'ogimet', verify=True)
except Exception as e:
    traceback.print_exc()
