import math
from typing import Dict, Union, Literal

try:
    from scipy.stats import norm
except ImportError:
    # Fallback to standard math implementation if scipy is not available
    def normal_cdf(x):
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0
    
    class norm:
        cdf = normal_cdf
        @staticmethod
        def pdf(x):
            return math.exp(-0.5 * x**2) / math.sqrt(2 * math.pi)

class OptionsPricingEngine:
    """
    Computes Options Pricing and Greeks using the Black-Scholes-Merton model.
    Used by StrategyLab to simulate non-linear instrument returns (like Nifty Options)
    incorporating time decay (Theta) and Volatility Crush (Vega).
    """

    @staticmethod
    def _d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
        """
        Calculates d1 and d2 parameters for Black-Scholes.
        S = Spot Price
        K = Strike Price
        T = Time to Maturity (in years)
        r = Risk-free interest rate
        sigma = Implied Volatility
        """
        # Handle expiration precisely
        if T <= 0.0 or sigma <= 0.0:
            return float('inf') if S >= K else float('-inf'), float('inf') if S >= K else float('-inf')

        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return d1, d2

    @classmethod
    def calculate_price(
        cls, 
        option_type: Literal['call', 'put'], 
        S: float, 
        K: float, 
        T: float, 
        r: float, 
        sigma: float
    ) -> float:
        if T <= 0.0:
            return max(0.0, S - K) if option_type == 'call' else max(0.0, K - S)

        d1, d2 = cls._d1_d2(S, K, T, r, sigma)
        
        if option_type == 'call':
            return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        elif option_type == 'put':
            return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        else:
            raise ValueError("Option type must be 'call' or 'put'")

    @classmethod
    def calculate_greeks(
        cls, 
        option_type: Literal['call', 'put'], 
        S: float, 
        K: float, 
        T: float, 
        r: float, 
        sigma: float
    ) -> Dict[str, float]:
        if T <= 0.0:
            return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

        d1, d2 = cls._d1_d2(S, K, T, r, sigma)
        
        # Delta
        if option_type == 'call':
            delta = norm.cdf(d1)
        else:
            delta = norm.cdf(d1) - 1

        # Gamma (Same for Call/Put)
        gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))

        # Vega (Same for Call/Put, normally represented as effect of 1% change)
        vega = S * norm.pdf(d1) * math.sqrt(T) / 100.0

        # Theta (Normally represented as value decay per day)
        term1 = -(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
        if option_type == 'call':
            theta = term1 - r * K * math.exp(-r * T) * norm.cdf(d2)
        else:
            theta = term1 + r * K * math.exp(-r * T) * norm.cdf(-d2)
        theta_per_day = theta / 365.0

        # Rho (Effect of 1% interest rate change)
        if option_type == 'call':
            rho = K * T * math.exp(-r * T) * norm.cdf(d2) / 100.0
        else:
            rho = -K * T * math.exp(-r * T) * norm.cdf(-d2) / 100.0

        return {
            "delta": delta,
            "gamma": gamma,
            "vega": vega,
            "theta": theta_per_day,
            "rho": rho
        }

    @classmethod
    def build_option_chain_snapshot(
        cls, 
        S: float, 
        strikes: list[float], 
        T: float, 
        r: float, 
        sigma_surface: callable = None
    ) -> list[Dict]:
        """
        Dynamically builds a theoretical option chain for backtesting.
        sigma_surface: A function taking (strike, T) and returning the implied volatility.
        """
        chain = []
        for strike in strikes:
            iv = sigma_surface(strike, T) if sigma_surface else 0.20 # default flat surface at 20%
            
            call_price = cls.calculate_price('call', S, strike, T, r, iv)
            put_price = cls.calculate_price('put', S, strike, T, r, iv)
            call_greeks = cls.calculate_greeks('call', S, strike, T, r, iv)
            put_greeks = cls.calculate_greeks('put', S, strike, T, r, iv)
            
            chain.append({
                "strike": strike,
                "implied_vol": iv,
                "dte": T * 365,
                "call": {"price": call_price, **call_greeks},
                "put": {"price": put_price, **put_greeks}
            })
        return chain
