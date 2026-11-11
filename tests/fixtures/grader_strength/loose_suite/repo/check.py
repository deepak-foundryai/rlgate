import sys

import calc

# g is checked, but loosely: any g(1) above 2 passes, so `x + 3` survives
sys.exit(0 if calc.f(1) == 2 and calc.g(1) > 2 else 1)
