import websocket
import json
import threading
import asyncio
import os
import numpy as np
import matplotlib.pyplot as plt
from deriv_api import DerivAPI, APIError

# Constants
APP_ID = '0000'  # replace with your APP ID
URL = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"
api_token = os.getenv('DERIV_TOKEN', ' ')  # replace with your API token

# Configuration
BET_AMOUNT = 300
MAX_BET_AMOUNT = 20000
TOTAL_ROUNDS = 10
TAKE_PROFIT = 5000
STOP_LOSS = 50000
RSI_PERIOD = 14

# Initialize historical data and metrics
tick_data = []
total_wins = 0
total_losses = 0

# Data for plotting
plot_ticks = []
plot_trades = []

def update_data(tick):
    global tick_data
    if len(tick_data) >= RSI_PERIOD:
        tick_data.pop(0)
    tick_data.append(float(tick))
    plot_ticks.append(float(tick))

def calculate_rsi(series, period):
    if len(series) < period:
        return None
    gains = np.zeros(period)
    losses = np.zeros(period)
    for i in range(1, period):
        change = series[i] - series[i-1]
        if change > 0:
            gains[i] = change
        else:
            losses[i] = -change
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 1)  # Round RSI to one decimal place

def interpret_rsi(rsi_value):
    """Interpret RSI based on rounded value for even/odd prediction."""
    if rsi_value is None:
        return None

    # Approximate RSI value to decide on trade
    if rsi_value % 2 == 0:
        return 'even', rsi_value
    else:
        return 'odd', rsi_value

def on_message(ws, message):
    data = json.loads(message)
    if 'error' in data:
        print('Error:', data['error']['message'])
        ws.close()
    elif data['msg_type'] == 'history':
        ticks = data['history']['prices']
        for tick in ticks:
            update_data(tick)
    elif data['msg_type'] == 'tick':
        tick = data['tick']['quote']
        update_data(tick)

def on_error(ws, error):
    print("WebSocket error:", error)

def on_close(ws):
    print("WebSocket connection closed")

def on_open(ws):
    def run(*args):
        TICKS_REQUEST = {
            "ticks_history": "R_100",
            "adjust_start_time": 1,
            "count": RSI_PERIOD,
            "end": "latest",
            "start": 1,
            "style": "ticks",
            "subscribe": 1
        }
        ws.send(json.dumps(TICKS_REQUEST))
        
    threading.Thread(target=run).start()

def subscribe_ticks():
    websocket.enableTrace(False)
    ws = websocket.WebSocketApp(URL,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    ws.on_open = on_open
    ws.run_forever()

async def sample_calls():
    global total_wins, total_losses, plot_trades

    api = DerivAPI(app_id=APP_ID)
    
    await api.authorize(api_token)
    balance = await api.balance()
    initial_balance = balance['balance']['balance']
    current_balance = initial_balance
    print(f"Initial balance: {initial_balance}")
    
    round_num = 1
    
    while round_num <= TOTAL_ROUNDS:
        await asyncio.sleep(1)

        if len(tick_data) >= RSI_PERIOD:
            rsi_value = calculate_rsi(tick_data, RSI_PERIOD)
            print(f"Round {round_num}: RSI Value: {rsi_value}")

            condition, rsi_value = interpret_rsi(rsi_value)
            
            if condition is None:
                print(f"Round {round_num}: Not enough data for RSI calculation. Skipping trade.")
                round_num += 1
                continue

            if condition == 'even':
                contract_type = "DIGITEVEN"
                print(f"Round {round_num}: RSI indicates even. Placing {contract_type} trade.")
            elif condition == 'odd':
                contract_type = "DIGITODD"
                print(f"Round {round_num}: RSI indicates odd. Placing {contract_type} trade.")
            else:
                print(f"Round {round_num}: RSI is neutral. Skipping trade.")
                round_num += 1
                continue

            # Place trade
            try:
                proposal = await api.proposal({
                    "proposal": 1,
                    "amount": BET_AMOUNT,
                    "barrier": "0",
                    "basis": "payout",
                    "contract_type": contract_type,
                    "currency": "USD",
                    "duration": 1,
                    "duration_unit": "t",
                    "symbol": "R_100"
                })
                proposal_id = proposal.get('proposal', {}).get('id')
                if not proposal_id:
                    print("Failed to get proposal")
                    round_num += 1
                    continue
            except APIError as e:
                print(f"Failed to get proposal: {e}")
                round_num += 1
                continue
            
            try:
                buy_response = await api.buy({"buy": proposal_id, "price": BET_AMOUNT})
                contract_id = buy_response.get('buy', {}).get('contract_id')
                if not contract_id:
                    print("Failed to get contract ID")
                    round_num += 1
                    continue
            except APIError as e:
                print(f"Failed to buy: {e}")
                round_num += 1
                continue

            # Check profit/loss
            try:
                profit_table = await api.profit_table({"profit_table": 1, "limit": 1})
                if profit_table and 'profit_table' in profit_table:
                    last_trade = profit_table['profit_table']['transactions'][0]
                    sell_price = last_trade['sell_price']
                    
                    if sell_price == 0:
                        total_losses += 1
                        print(f"Round {round_num}: Loss. Sell price: {sell_price}. Current balance: {current_balance}.")
                    else:
                        total_wins += 1
                        current_balance += sell_price - BET_AMOUNT
                        print(f"Round {round_num}: Win. Sell price: {sell_price}. Current balance: {current_balance}.")
                    
                    # Record trade
                    plot_trades.append({
                        'round': round_num,
                        'tick': float(tick_data[-1]),
                        'trade_type': contract_type,
                        'sell_price': sell_price
                    })

                    # Update balance
                    balance = await api.balance()
                    current_balance = balance['balance']['balance']
                    print(f"Updated balance: {current_balance}")

                    # Stop conditions
                    if current_balance - initial_balance >= TAKE_PROFIT:
                        print("Take profit reached. Stopping trading.")
                        break
                    elif initial_balance - current_balance >= STOP_LOSS:
                        print("Stop loss reached. Stopping trading.")
                        break

            except APIError as e:
                print(f"Failed to retrieve profit table: {e}")

        round_num += 1

    await api.disconnect()

    # Plot results
    plt.figure(figsize=(12, 6))
    ticks = [tick for tick in plot_ticks]
    trades = [trade['tick'] for trade in plot_trades]
    trade_types = ['Buy' if trade['trade_type'] == 'DIGITODD' else 'Sell' for trade in plot_trades]

    plt.plot(ticks, label='Ticks', color='blue')
    plt.scatter(range(len(trades)), trades, c='red', marker='o', label='Trades')
    for i, txt in enumerate(trade_types):
        plt.annotate(txt, (i, trades[i]), textcoords="offset points", xytext=(0,10), ha='center')

    plt.xlabel('Round')
    plt.ylabel('Tick Value')
    plt.title('Ticks and Trades')
    plt.legend()
    plt.show()

if __name__ == "__main__":
    subscribe_thread = threading.Thread(target=subscribe_ticks)
    subscribe_thread.start()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(sample_calls())
