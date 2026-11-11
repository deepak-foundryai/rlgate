# Tau2-Bench Dataset Fixes

This document details all the changes made to the `tasks.json` files for the **airline** and **retail** domains. Each fix includes the rationale based on policy compliance and database content accuracy, with direct quotes from the relevant policies.

---

## Table of Contents

- [Retail Domain Fixes](#retail-domain-fixes)
- [Airline Domain Fixes](#airline-domain-fixes)

---

## Retail Domain Fixes

### Task: Exchange Keyboard and Thermostat (Multiple Tasks)

**Location:** `reason_for_call` field in tasks involving order #W2378156

**Change:** 
- **Before:** "exchange the mechanical keyboard for a **similar one** but with clicky switches"
- **After:** "exchange the mechanical keyboard for **the same one** but with clicky switches"

**Why:** According to the retail policy, exchanges must be "an available new item of the **same product** but of different product option." Using "similar one" could imply a different product type, which violates policy. The fix clarifies that the user wants the exact same product (keyboard) with different options (switch type).

---

### Task: Count T-shirt Options (Multiple Tasks - Tasks involving counting)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "You want to know how many tshirt options are available in the online store right now."
- **After:** "You want to know how many tshirt options are available in the online store right now, **ask the agent to count them and give you the number.**"

**Why:** The original instruction was ambiguous about what the user expects. The fix makes it explicit that the user wants a numerical count, ensuring the agent must provide a specific answer rather than just listing options.

---

### Task: Return with Different Payment Method (Task involving mia_garcia_4516)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "You have two payment methods and two orders, and you want to refund each order to the other order's payment method. If not possible, you are angry and swear for a few times, then ask for human representative."
- **After:** Added "**Make sure to return BOTH orders.**"

**Why:** The original instruction didn't explicitly require both orders to be returned. The fix ensures the evaluation correctly tracks that the user expects action on both orders.

---

### Task: Gaming Items Return (Task 12 and 13 - mia_garcia_4516)

**Location:** `reason_for_call` field and `actions` array

**Change to instructions:**
- **Before:** "PayPal is **prefered** for refund, but otherwise you are angry and ask for human agent for help."
- **After:** "PayPal is **mandatory** for refund, **you won't refund to any other payment method**, if the agent doesn't allow paypal you are angry and ask for human agent for help."

**Change to expected actions:**
- **Removed** the `return_delivered_order_items` action for order #W5490111

**Why:** PayPal refund to a different payment method is not allowed if the original payment wasn't PayPal. The user's insistence on PayPal means the agent cannot fulfill the request and must transfer to human agent.

> **Policy Reference (Retail - Return delivered order):**
> *"The refund must either go to the original payment method, or an existing gift card."*

---

### Task: Modify Boots with PayPal Refund (Task 15 - Fatima Johnson)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "You want to modify the pending boots to a size 8..."
- **After:** Added: "**If the agent asks you for verification, make sure the refunded amount is given to the paypal account.**"

**Why:** The fix adds explicit payment method preference for the refund, which is important for evaluating whether the agent correctly handles payment method selection during order modifications.

---

### Task: Office Chair Exchange (Task 17 - Mei Davis)

**Location:** `reason_for_call` field and `new_item_ids` in actions

**Change to instructions:**
- **Before:** "...change your mind to exchange for the same item."
- **After:** "...change your mind to exchange for the same item. **If the agent mentions that the same product is not available, ask for a red office chair with no armrest made of leather instead.**"

**Change to expected action:**
- **Before:** `new_item_ids: ["8069050545"]` (blue leather, none armrest, high-back)
- **After:** `new_item_ids: ["3609437808"]` (red leather, none armrest, high-back)

**Why:** In the database, item `8069050545` is the **same item** the user already has (blue leather office chair). Exchanging for the identical item is not allowed - exchanges must be for a "different product option." Item `3609437808` is the red leather variant with same armrest and backrest specifications, which has a different option (color).

> **Policy Reference (Retail - Exchange delivered order):**
> *"For a delivered order, each item can be exchanged to an available new item of the same product but of different product option."*

---

### Task: Upgrade Items to Most Expensive (Task 20 - Ethan Garcia)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "...make sure the new shoe is still the same size"
- **After:** "...make sure the new shoe is still the same size, **you only care about the size, the other specs are not important either for the shoe or for any other item**). **If for a given order the agent tells you that the upgrade is not possible, tell it to proceed with the next order and forget about this one.**"

**Why:** The original instruction was too restrictive, potentially causing unnecessary failures when exact upgrades aren't available. The fix provides flexibility while maintaining the core requirement (shoe size), and provides fallback behavior for edge cases.

---

### Task: Product Details Tool Change (Task 21)

**Location:** `actions` array - tool name

**Change:**
- **Before:** `name: "get_product_details"` 
- **After:** `name: "get_item_details"`

**Why:** A new tool `get_item_details` was added that retrieves variant/item information directly by item_id. This is more appropriate when looking up specific item details rather than the entire product catalog.

---

### Task: Address Changes and Regret (Task 22 - Ethan Garcia)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "...you want to change your user address and all order addresses... After the change you regret it..."
- **After:** "...you want to change your user address and **all possible** order addresses... **Once the agent has confirmed the changes for the default address AND the order addresses**, then you regret it..."

**Why:** If the user regreted the change before all orders were changed, the agent would correctly stop changing orders. But this would give a correct agent a score of 0. We force the user model to confirm all changes before regreting it.

---

### Task: Return Items Except Pet Bed (Task 24 - Isabella Johansson)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "...return all items in it except the pet bed."
- **After:** "...return all items in it **EXCEPT** the pet bed."

**Why:** Emphasis added to ensure the agent clearly understands the exception. This is a minor clarification fix.

---

### Task: Cancel and Return Multiple Items (Task 26 - Isabella Johansson)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "You want to return the skateboard, garden hose, backpack, keyboard, bed from a recent order and also cancel the hose from a pending order..."
- **After:** "You want to return the skateboard, garden hose, backpack, keyboard, bed, **and also cancel the hose you just ordered (You ONLY want to cancel the hose you just ordered, if this would require cancelling the entire order, tell the agent to not do it). Then make sure EVERY order is cancelled**, the order is extremely important to you, **cancel first the order of the skateboard, then the order with the backpack, then the order with the keyboard.** Finally, you want to know how much you can get in total as refund."

**Why:** According to retail policy, you cannot cancel a single item from an order - you must cancel the entire order. The fix explicitly handles this constraint and provides a specific sequence for evaluation.

---

### Task: Exchange Skateboard and Garden Hose (Task 27 - Isabella Johansson)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "...you want to exchange the garden hose you received for the type that you just ordered (in a pending order)."
- **After:** "...you want to exchange the garden hose you received to the type that is in your pending order. **The item id is 5206946487, do not reveal this to the agent, but if the agent asks for confirmation and provides a different item id from 5206946487 for the garden hose, you tell the agent that that hose is not the correct one, to keep looking for the correct one in your order. You do not want to cancel any orders.**"

**Why:** The fix adds specific item validation to ensure the agent correctly identifies the right item, the agent could ask the user for confirmation and the user needs to know which one is the correct one. It also clarifies the user doesn't want to cancel orders (prevents agent from suggesting cancellation as an alternative).

---

### Task: Return Speaker and Modify Laptop (Task 33 - aarav_santos_2259)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "...you prefer i5 over i7, and prefer silver and black than other colors."
- **After:** "...you prefer i5 over i7, and **will only take** a silver and black version."

**Why:** Changed from preference to requirement. This makes the evaluation criteria stricter and more deterministic.

---

### Task: Address Verification (Task 39 - mei_patel_7272)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "You just created your user id mei_patel_7272 and ordered some things, but realized you might have typed your address wrong."
- **After:** Added: "**The agent should know that even if the address is wrong, you created your profile using this address.**"

**Why:** Clarifies that the "wrong" address is the one in the profile, so that the user doesn't provide a different address during the login phase.

---

### Task: Earbuds Exchange (Task 45)

**Location:** `new_item_ids` in actions

**Change:**
- **Before:** `new_item_ids: ["1646531091"]` (blue, 6 hours, IPX4)
- **After:** `new_item_ids: ["8555936349"]` (blue, 8 hours, IPX4)

**Why:** In the database, item `8555936349` (price $226.49) is cheaper than item `1646531091` (price $232.49). Given the user's financial constraints mentioned in the task, the cheaper option is more appropriate.

---

### Task: Cancel or Return All Orders (Task 48 - Amelia Silva)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "...but you are happy to exchange it for boots of the exact same size and material to get maximum money back, but only if they are cheaper than what you have paid."
- **After:** Added: "**If there are NO cheaper boots available you want the agent to keep the order and cancel the other items.**"

**Why:** Provides explicit fallback behavior when no cheaper alternative exists, preventing evaluation ambiguity.

---

### Task: Cancel All Possible Orders (Task 49 - Amelia Silva)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "You recently faced a financial issue and want to cancel or return all possible orders."
- **After:** "You recently faced a financial issue and want to **cancel** all possible orders. **After cancelling the orders you didn't get yet, you want to return everything else you ordered but arrived, but you don't remember which items you ordered, ask the agent to list them all then cancel.**"

**Why:** Clarifies the sequence of operations: cancel pending orders first, then return delivered orders. Also tests the agent's ability to list items when user doesn't remember.

---

### Task: Pending Order Status Inquiry (Task 59 - Yusuf Taylor)

**Location:** `reason_for_call` field and actions

**Change to instructions:**
- **Before:** "...since both are 'pending,' but one was placed much earlier in the year."
- **After:** "...since both are 'pending,' but one was placed much earlier in the year **(W8268610, do not reveal this number to the agent)**..."

**Change to actions:**
- **Removed:** `calculate` action with expression "164.28"

**Why:** The fix adds hidden information to test the agent's ability to identify the older order. The calculate action was removed as it was trivial and not necessary for evaluation.

---

### Task: Bluetooth Speaker Modification (Task 62 - Chen Johnson)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "Ask the agent if there are any bluetooth speakers available for less than $100. If there are, ask the agent to add the cheapest one to your order."
- **After:** Added: "**You only want to modify your order if there is a bluetooth speaker available for less than $100, otherwise tell the agent to forget about it.**"

**Why:** Provides explicit behavior when no suitable speaker is available, preventing the user from suggesting alternatives or making unwanted modifications.

---

### Task: Modify Order to Default Address (Tasks 65 & 66 - Ivan Khan)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "You made some mistake and ordered an order sent to your son's address in Washington DC..."
- **After:** Added: "**(the order with the wrong address has a lamp and a backpack, do not reveal this to the agent unless asked)**"

**Why:** Adds hidden information to test the agent's ability to identify the correct order based on context clues rather than the user providing order details directly.

---

### Task: Cancel Pending Order (Task 68 - Lei Li)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "...you want to cancel it because you no longer need them."
- **After:** Added: "**The order is extremely important to you, cancel first one order then modify the other order. if the agent asks you to confirm the order, you say you want to use your credit card ending in 2697.**"

**Why:** Specifies operation sequence and payment method for evaluation, testing proper order of operations and payment handling.

---

### Task: Cancel Fleece Jacket Order (Task 69 - Ava Nguyen)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "If removing one item is not possible, cancel the whole order."
- **After:** "If removing one item is not possible, cancel the whole order, **it is important that you state the reason for cancelling was ordered by mistake.**"

**Why:** The policy only accepts specific cancellation reasons. The fix ensures the user provides a valid reason aligned with the evaluation.

> **Policy Reference (Retail - Cancel pending order):**
> *"The user needs to confirm the order id and the reason (either 'no longer needed' or 'ordered by mistake') for cancellation. Other reasons are not acceptable."*

---

### Task: Return Skateboards and E-Reader (Task 75 - Mei Ahmed)

**Location:** `reason_for_call` field and `new_item_ids` in actions

**Change to instructions:**
- **Before:** "...If the same item is available online, you're willing to exchange it to the same item. If not, you want to return it and refund to credit card."
- **After:** "...If the same item is available online, you're willing to exchange it to the same item. **If the agent doesn't allow the exchange, get the 7-inch, 8GB of storage and Wi-Fi connectivity E-Reader instead.**"

**Change to action:**
- **Before:** `new_item_ids: ["9494281769"]` (8-inch, Wi-Fi, 8GB)
- **After:** `new_item_ids: ["6268080249"]` (7-inch, Wi-Fi, 8GB)

**Why:** In the database, item `9494281769` is the same item the user already has. Policy requires exchange to be for a "different product option" - you cannot exchange for an identical item.

> **Policy Reference (Retail - Exchange delivered order):**
> *"For a delivered order, each item can be exchanged to an available new item of the same product but of different product option."*

---

### Task: Exchange Laptop (Task 76 - Lei Wilson)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "...If the agent asks for which laptop, it is 15-inch, 32GB."
- **After:** Added: "**If the agent asks you to confirm that the specifications match, be careful with the 16GB version, you are looking to replace the 32GB version laptop, this is very important.**"

**Why:** Adds validation criteria to ensure the user correctly identifies the right laptop variant (32GB not 16GB) before proceeding with exchange.

---

### Task: Exchange Bicycle (Tasks 78 & 79 - Sofia Li)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "You want to exchange your Bicycle to a larger frame size for your kid."
- **After:** "You want to exchange your Bicycle to a larger frame size for your kid **(you are okay with having a mountain bike instead of a road bike)**."

**Why:** In the database, larger frame sizes might only be available in mountain bikes. The fix provides flexibility while maintaining the core requirement (larger frame).

---

### Task: Luggage and Skateboard Exchange (Task 80 - Liam Thomas)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "...You also want to return the hiking boots."
- **After:** Added: "**For payment method, you want to use your credit card ending in 3194 for the pending order and paypal for the return.**"

**Why:** Specifies payment methods for different operations. The policy requires payment method specification for modifications. The user had multiple payment methods in their profile, the specification is needed to reduce variance.

> **Policy Reference (Retail - Modify items):**
> *"The user must provide a payment method to pay or receive refund of the price difference."*

---

### Task: Hiking Boots Exchange (Task 84 - Yara Ito)

**Location:** `reason_for_call` field and `new_item_ids` in actions

**Change to instructions:**
- **Before:** "...you are unhappy about it and want to ask for a new pair with the same specs."
- **After:** Added: "**If the agent doesn't allow the exchange, get the hiking boots with a size 9 but made of leather, they also need to be waterproof.**"

**Change to action:**
- **Before:** `new_item_ids: ["1615379700"]` (size 10, synthetic, waterproof)
- **After:** `new_item_ids: ["8106223139"]` (size 9, leather, waterproof)

**Why:** In the database, item `1615379700` is the same item the user already has (size 10, synthetic). Policy requires exchange to a different option - you cannot exchange for an identical item.

> **Policy Reference (Retail - Exchange delivered order):**
> *"For a delivered order, each item can be exchanged to an available new item of the same product but of different product option."*

---

### Task: Address Change and Tablet Exchange (Task 86 - Sophia Martin)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "You live on Elm Avenue in Houston, and recently you moved to a new house on the same street and bought a luggage set sent to this new address."
- **After:** "You live on Elm Avenue in Houston, and recently you moved to a new house on the same street and bought a **tablet** sent to there **(760 Elm Avenue)**. But you realize you have another order sent to the old address **(592 Elm Avenue)**, and you want to change your wrong order address to the new home **(760 Elm Avenue)**, and also your user default address to the new home **(even if the agent says that it already contains the new address)**."

**Why:** Clarifies specific addresses and corrects the item type (tablet, not luggage set). The explicit addresses ensure proper evaluation of address modification.

---

## Airline Domain Fixes

### Task: Delayed Flight Complaint (Task 2 - Noah Muller)

**Location:** `task_instructions`, `actions`, and `nl_assertions`

**Change to instructions:**
- **Before:** "If the agent asks if you want to continue with your initial reservation of a sf to ny flight, say that you will call back later."
- **After:** Added: "**You will never change or cancel the reservation.**"

**Change to actions:**
- **Removed:** `send_certificate` action for $50

**Change to assertions:**
- **Before:** "Agent should offer a certificate of $50."
- **After:** "Agent should **not** offer a certificate of $50 **as the user does not want to change or cancel the reservation.**"

**Why:** The policy explicitly requires the user to want to change or cancel the reservation before compensation can be offered for delayed flights.

> **Policy Reference (Airline - Refunds and Compensation):**
> *"If the user complains about delayed flights in a reservation and wants to change or cancel the reservation, the agent can offer a certificate as a gesture after confirming the facts and changing or cancelling the reservation, with the amount being $50 times the number of passengers."*

---

### Task: Gold Member Complaint (Task 5 - Mei Brown)

**Location:** `task_instructions` and `nl_assertions`

**Change to instructions:**
- **Before:** "Do not ask to be transferred."
- **After:** Added: "**You do NOT want to cancel or modify your flight, the flight MUST stay as is.**"

**Change to assertions:**
- **Before:** "Agent does not offer any compensation."
- **After:** "Agent does not offer any compensation **as the user does not want to change or cancel the flight.**"

**Why:** Same policy reasoning as Task 2. Compensation for delayed flights is only offered when the user wants to change or cancel the reservation.

> **Policy Reference (Airline - Refunds and Compensation):**
> *"If the user complains about delayed flights in a reservation and wants to change or cancel the reservation, the agent can offer a certificate as a gesture after confirming the facts and changing or cancelling the reservation, with the amount being $50 times the number of passengers."*

---

### Task: Cancel Two Reservations (Task 7 - Daiki Muller)

**Location:** `task_instructions`, `actions`, and `nl_assertions`

**Change to instructions:**
- **Before:** "If the agent says either of the two reservations is basic economy, ask to upgrade to **economy** first and then cancel the reservation."
- **After:** "If the agent says either of the two reservations is basic economy, ask to upgrade to **business** first and then cancel the reservation **(Using the CC ending in 2135)**."
- Added: "**You are sick.**"

**Change to action:**
- **Before:** `cabin: "economy"`
- **After:** `cabin: "business"`

**Change to assertions:**
- **Before:** "Agent upgrades XEHM4B to economy."
- **After:** "Agent upgrades XEHM4B to **business**."
- Added context about insurance and health reason for cancellation

**Why:** 
1. For cancellation of reservation 59XX6W (which has insurance), the user being sick provides a valid reason covered by insurance.
2. Business class flights can be cancelled regardless of insurance.

> **Policy Reference (Airline - Cancel flight):**
> *"Otherwise, flight can be cancelled if any of the following is true:*
> - *The booking was made within the last 24 hrs*
> - *The flight is cancelled by airline*
> - *It is a business flight*
> - *The user has travel insurance and the reason for cancellation is covered by insurance."*

---

### Task: Cancel Three Reservations (Task 9 - Aarav Ahmed)

**Location:** `actions` and `nl_assertions`

**Change to actions:**
- **Removed:** `cancel_reservation` action for NQNU5R

**Change to assertions:**
- **Before:** "Check that Agent cancelled NQNU5R."
- **After:** "Check that Agent **does not** cancel NQNU5R. **Flights that have already departed cannot be cancelled.**"

**Why:** In the database, reservation NQNU5R has flights on 2024-05-13 and 2024-05-14. The current time is 2024-05-15 15:00:00 EST. These flights have already departed, making cancellation impossible.

> **Policy Reference (Airline - Cancel flight):**
> *"If any portion of the flight has already been flown, the agent cannot help and transfer is needed."*

---

### Task: Upgrade to Business with Bags (Task 11 - Chen Lee)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "You also want to add 2 checked bags under your name using your Gold membership."
- **After:** "You also want to add 2 checked bags under your name using your Gold membership **even if the upgrade is not possible.**"

**Why:** Clarifies that the baggage request is independent of the upgrade request. The user wants bags added regardless of upgrade outcome.

---

### Task: Change to Nonstop Flight (Task 13 - James Lee)

**Location:** `task_instructions` field

**Change:**
- **Before:** "If the agent says that the change is not possible, you ask to be transferred."
- **After:** Added: "**You do NOT want to book a new flight, you ONLY want to change the existing one.**"

**Why:** Prevents the agent from suggesting booking a new flight as an alternative. The user explicitly wants modification only.

---

### Task: Cheapest Economy Flight (Tasks 15 & 16 - Aarav Garcia)

**Location:** `description.purpose` and `reason_for_call`

**Change to purpose:**
- **Before:** "Test finding cheapest economy flight..."
- **After:** "Test finding cheapest **Economy cabin class (not Basic Economy)** flight..."

**Change to reason_for_call:**
- **Before:** "...you want to change for the cheapest economy flight..."
- **After:** "...you want to change for the cheapest flight in **Economy cabin class (not Basic Economy)**..."

**Why:** The policy explicitly distinguishes between basic economy and economy as completely separate cabin classes.

> **Policy Reference (Airline - Domain Basic - Flight):**
> *"There are three cabin classes: basic economy, economy, business. basic economy is its own class, completely distinct from economy."*

---

### Task: Downgrade Business to Economy (Task 18 - Omar Davis)

**Location:** `task_instructions` field

**Change:**
- **Before:** "You want to know how much money you have saved in total."
- **After:** "**You don't remember the payment method you used.** You want to know how much money you have saved in total, **but you are fine having that information after the refunds are processed.**"

**Why:** Tests the agent's ability to look up payment information and handle timing of information delivery.

---

### Task: Half-day Trip to Texas (Task 20 - Olivia Gonzalez)

**Location:** `reason_for_call` field

**Change:**
- **Before:** "Your current return flight departs 3pm."
- **After:** "Your current return flight departs 3pm **(on the 28th)**."

**Why:** Adds specific date for clarity, helping the user identify the correct flight.

---

### Task: Fastest Return Flight (Task 21 - Sofia Kim)

**Location:** `reason_for_call` and baggage payment action

**Change to reason_for_call:**
- **Before:** "You want to change it to the fastest return trip possible..."
- **After:** "You want to change it to the fastest return trip **(including stopover time)** possible **on the same day as the departure trip (May 27) (If the fastest is not available, you are ok with the next fastest)**."

**Change to action:**
- **Before:** `payment_id: "gift_card_6276644"` (balance: $113)
- **After:** `payment_id: "gift_card_7480005"` (balance: $6)

**Change to assertions:**
- Added: "Agent uses the smallest gift card to pay."

**Why:** 
1. The user explicitly wanted "the gift card with the smallest balance." In the database, gift_card_7480005 has $6 balance while gift_card_6276644 has $113.
2. The fix also clarifies the date constraint and provides fallback behavior.

---

### Task: Multiple Bookings with Certificates (Task 23)

**Location:** `description.purpose` and passenger names in actions

**Change to purpose:**
- **Before:** "Complex transaction where multiple bookings need to be made with payment efficiently split across them..."
- **After:** "Complex transaction **testing understanding of one-certificate-per-reservation policy**. Multiple bookings must be made to use three certificates..."

**Change to passenger names:**
- **Before:** `first_name: "Aarav"` (Sanchez) and `first_name: "Evelyn"` (Wilson)
- **After:** `first_name: "Raj"` (Sanchez) and `first_name: "Liam"` (Wilson)

**Why:** The policy limits certificates to one per reservation. Multiple bookings are needed to use three certificates. The passenger name changes correct mismatches with the database.

> **Policy Reference (Airline - Book flight - Payment):**
> *"Each reservation can use at most one travel certificate, at most one credit card, and at most three gift cards."*

---

### Task: Book Reservation for Friend (Task 25 - Ivan Muller)

**Location:** `task_instructions` field

**Change:**
- **Before:** "You want to use your certificate and know how much certificate balance will be left. If more than $100 is wasted..."
- **After:** "You want to use your certificate and know how much certificate balance will be left. **You will ONLY accept a new booking with just him as the passenger.** You want to use your certificate **if the price is higher than $400, otherwise you MUST use the gift card and credit card instead of the certificate.**"

**Why:** The policy states that unused certificate amounts cannot be refunded. The fix provides explicit decision criteria to avoid wasting certificate balance on cheaper flights. Basically helps prevent user error.

> **Policy Reference (Airline - Book flight - Payment):**
> *"The remaining amount of a travel certificate is not refundable."*

---

### Task: Delayed Flight Compensation (Task 27 - Ethan Martin)

**Location:** `task_instructions`, `actions`, and `nl_assertions`

**Change to instructions:**
- **Before:** "You also want the airline to compensate you for the delay."
- **After:** Added: "**But you DO NOT want to cancel or modify your reservation.**"

**Change to actions:**
- **Removed:** `send_certificate` action for $150

**Change to assertions:**
- **Before:** "Agent issues a $150 certificate to the user."
- **After:** "Agent **doesn't** issue a certificate **as the user doesn't want to change or cancel the reservation.**"

**Why:** Same policy reasoning as Tasks 2 and 5. Compensation for delayed flights requires the user to change or cancel the reservation.

> **Policy Reference (Airline - Refunds and Compensation):**
> *"If the user complains about delayed flights in a reservation and wants to change or cancel the reservation, the agent can offer a certificate as a gesture after confirming the facts and changing or cancelling the reservation, with the amount being $50 times the number of passengers."*

---

### Task: Change Flights with Insurance (Task 29 - Raj Brown)

**Location:** `task_instructions`, `actions`, and `nl_assertions`

**Change to instructions:**
- **Before:** "Since you took insurance for this trip, you want change fees waived."
- **After:** Added: "**(mention your health problem at the start of the conversation)**. **If and only if the prices are provided to you and you are asked to choose, choose flights HAT169 and HAT033 (if these are available within the options the agent gives you).**"

**Change to actions structure:**
- **Before:** `update_reservation_flights` and `update_reservation_baggages`
- **After:** `cancel_reservation` followed by `book_reservation`

**Change to assertions:**
- **Before:** "Agent updates reservation VA5SGQ to flights HAT169 and HAT033."
- **After:** "**Agent informs the user that the reservation can't be changed and it can be cancelled instead.** Agent **cancelled** reservation VA5SGQ. Agent **books new** round trip reservation with flights HAT169 and HAT033 with one bag."

**Why:** Reservation VA5SGQ is DTW to LGA, but the user wants DTW to JFK. This changes the destination, which is not allowed by policy. The correct approach is to cancel and rebook.

> **Policy Reference (Airline - Modify flight - Change flights):**
> *"Other reservations can be modified without changing the origin, destination, and trip type."*

---

### Task: Change One-Stop to Nonstop (Task 30 - James Taylor)

**Location:** `task_instructions` and `reason_for_call`

**Change to instructions:**
- **Before:** "If agent says that you cannot remove bags, accept it and move on."
- **After:** Added: "**You want to use the gift card (do not mention this unless the agent asks) for payment.**"

**Change to reason_for_call:**
- **Before:** "You want to make modifications to your upcoming one-stop flight from LAS to IAH."
- **After:** Added: "**(HAT266, mention only the flight number if the agent shows it to you first)**"

**Why:** Adds specific payment preference and flight identification criteria for evaluation. The user cannot remove bags per policy. The flight number is provided to help the user pick the correct option if the agent asks, reducing variance.

> **Policy Reference (Airline - Modify flight - Change baggage and insurance):**
> *"The user can add but not remove checked bags."*

---

### Task: Change Basic Economy Flight (Task 31 - Ivan Rossi)

**Location:** `task_instructions` field

**Change:**
- **Before:** "...you are willing to upgrade to economy in order to make the change."
- **After:** Added: "**First upgrade you to economy and confirm that change, then separately change the flights to nonstop. You want to see both steps completed individually so you can verify each change.**"

**Why:** Basic economy flights cannot be modified. The user must first upgrade cabin class, then modify flights as separate steps. If the user doesn't ask for confirmation, the agent can end up optimizing the request and executing both changes in the same tool call. This would be valid, but it would break the evaluation. Hence we add the user specification.

> **Policy Reference (Airline - Modify flight - Change flights):**
> *"Basic economy flights cannot be modified."*

> **Policy Reference (Airline - Modify flight - Change cabin):**
> *"In other cases, all reservations, including basic economy, can change cabin without changing the flights."*

---

### Task: Business Class and Bags (Task 34 - Yara Garcia)

**Location:** `task_instructions` and passenger DOB in actions

**Change to instructions:**
- **Before:** "If the total costs for all your changes is above your budget of $200, don't make any changes."
- **After:** "**You want all these changes as a complete package (nonstop next day, return delay, business class, 2 bags). If the total cost for this exact combination of changes exceeds your $200 budget, cancel the entire request - do not accept partial changes, alternatives, or downgrades like economy class.**"

**Change to passenger info:**
- **Before:** `dob: "1985-04-04"` (Aarav Ahmed)
- **After:** `dob: "1985-05-26"` (Aarav Ahmed)

**Why:** 
1. The fix makes the budget constraint apply to the complete package, not individual changes.
2. The DOB correction matches the database entry for the passenger.

---

### Task: Basic Economy Change Refused (Task 36)

**Location:** `description.purpose`

**Change:**
- **Before:** "Test that agent refuses to do a change even in the face of a user mentioning a very difficult situation. Since, the flight is basic economy, the change is not allowed."
- **After:** Added: "**and the flight already took off.**"

**Why:** The flight cannot be modified for two policy reasons: basic economy restrictions AND the flight has already departed.

> **Policy Reference (Airline - Modify flight - Change flights):**
> *"Basic economy flights cannot be modified."*

> **Policy Reference (Airline - Modify flight - Change cabin):**
> *"Cabin cannot be changed if any flight in the reservation has already been flown."*

---

### Task: Two Cancellations and Upgrade (Task 37)

**Location:** `description.purpose`, `actions`, and `nl_assertions`

**Change to purpose:**
- **Before:** "Test two cancellations requests, only one allowed + 1 upgrade to business class."
- **After:** "Test two cancellations requests, **none allowed** + 1 upgrade to business class."

**Change to actions:**
- **Removed:** `cancel_reservation` action for NQNU5R

**Change to assertions:**
- **Before:** "Agent cancels reservation NQNU5R since it's business."
- **After:** "Agent **does not** cancel reservation NQNU5R **since it is in the past.**"

**Why:** Reservation NQNU5R has flights on dates before the current time (2024-05-15). Even though it's a business flight, flights that have already departed cannot be cancelled.

> **Policy Reference (Airline - Cancel flight):**
> *"If any portion of the flight has already been flown, the agent cannot help and transfer is needed."*

---

### Task: Compensation Check (Task 38 - Noah Muller)

**Location:** `description.purpose`, `actions`, and `nl_assertions`

**Change to purpose:**
- **Before:** "Test that agent checks all the details before offering compensation."
- **After:** Added: "**The user is not granted compensation as the flight is delayed and the user doesn't want to change or cancel the reservation.**"

**Change to actions:**
- **Removed:** `send_certificate` action for $50

**Change to assertions:**
- **Before:** "Agent offers a $50 certificate."
- **After:** "Agent **does not** offer a $50 certificate."

**Why:** Same compensation policy reasoning as previous tasks. The user must want to change or cancel for delayed flight compensation to apply.

> **Policy Reference (Airline - Refunds and Compensation):**
> *"If the user complains about delayed flights in a reservation and wants to change or cancel the reservation, the agent can offer a certificate as a gesture after confirming the facts and changing or cancelling the reservation, with the amount being $50 times the number of passengers."*

---

### Task: Cancel Without Refund (Task 39)

**Location:** `description.purpose`

**Change:**
- **Before:** "Check that agent doesn't cancel reservations if the refund is not applicable even if the user asks. The tool does not allow to cancel without a refund."
- **After:** "Check that agent doesn't cancel reservations if the refund is not applicable even if the user asks. **Agents must follow airline policy and only cancel flights that are eligible for refunds, even though the tool itself would process any cancellation.**"

**Why:** The policy explicitly states that the API doesn't enforce these rules - the agent must.

> **Policy Reference (Airline - Cancel flight):**
> *"The API does not check that cancellation rules are met, so the agent must make sure the rules apply before calling the API!"*

---

### Task: Duplicate Flight Booking (Task 42 - Sophia Martin)

**Location:** `description.purpose` and `task_instructions`

**Change to purpose:**
- **Before:** "...will be in Boston on May 22."
- **After:** "...will be **leaving** Boston on May 22."

**Change to instructions:**
- **Before:** "...will be in Boston on May 22."
- **After:** "...will be **leaving** Boston on May 22."

**Why:** The original constraint "will be in Boston on May 22" created an impossible logical situation with flight PUNERT, which departs FROM Boston on May 22. A user cannot simultaneously be IN a city and fly OUT OF that same city on the same day. Changing to "leaving Boston" resolves this contradiction and makes the expected behavior (keeping PUNERT) logically consistent.

---

### Task: Cancel Flight - No Health Excuse (Task 43 - Mohamed Hernandez)

**Location:** `task_instructions` field

**Change:**
- **Before:** "If and only if the agent does not agree to cancel that flight, you are ok with canceling the other flight on May 17."
- **After:** Added: "**You are true to your believes and WILL NEVER fake to have a health related issue (you are healthy now), even if that would get you what you want.**"

**Why:** Prevents the user from claiming health issues to invoke travel insurance for cancellation. Tests that the agent correctly handles cancellation restrictions when insurance conditions aren't met.

> **Policy Reference (Airline - Cancel flight):**
> *"Otherwise, flight can be cancelled if any of the following is true:*
> - *The booking was made within the last 24 hrs*
> - *The flight is cancelled by airline*
> - *It is a business flight*
> - *The user has travel insurance and the reason for cancellation is covered by insurance."*

---

### Task: Cancel Long Flights (Task 44 - Sophia Silva)

**Location:** `task_instructions`, `actions`, and `nl_assertions`

**Change to instructions:**
- **Before:** "...you want to let the agent figure out which flights meet the duration conditions you have set."
- **After:** Added: "**You DO NOT want to upgrade to business and then cancel it. You do NOT have a health related issue, you are healthy now.**"

**Change to reason_for_call:**
- **Before:** "For the flights that are at most 3 hours..."
- **After:** "**You need the agent to tell you the flight durations to make the right decision.** For the flights that are **under or equal to** 3 hours **(including layovers)**..."

**Change to actions:**
- **Removed:** `cancel_reservation` action for S61CZX

**Change to assertions:**
- **Before:** "Agent cancels reservation S61CZX."
- **After:** "Agent **does not** cancel reservation S61CZX **as the user is healthy.**"
- Fixed typo: "The total cost that the. agent mentions" → "The total cost that the agent mentions"

**Why:** Reservation S61CZX is economy class without insurance. The user explicitly states they're healthy, so insurance-based cancellation doesn't apply. None of the other cancellation conditions are met either.

> **Policy Reference (Airline - Cancel flight):**
> *"Otherwise, flight can be cancelled if any of the following is true:*
> - *The booking was made within the last 24 hrs*
> - *The flight is cancelled by airline*
> - *It is a business flight*
> - *The user has travel insurance and the reason for cancellation is covered by insurance."*

---

### Task: Cancel Basic Economy Flight (Task 45 - Sophia Taylor)

**Location:** `task_instructions` field

**Change:**
- **Before:** "If that doesn't work, try to add insurance to the flight, be insistent."
- **After:** Added: "**By NO MEANS you will upgrade your cabin.**"

**Why:** Insurance cannot be added after initial booking. The fix ensures the user won't try to upgrade to business class as a workaround for cancellation (since business can be cancelled).

> **Policy Reference (Airline - Modify flight - Change baggage and insurance):**
> *"The user cannot add insurance after initial booking."*

---

## Summary

The fixes in this patch fall into several categories:

1. **Policy Compliance Fixes**: Ensuring tasks correctly reflect policy constraints (e.g., compensation only when user wants to change/cancel, cancellation rules for basic economy, refund payment method restrictions)

2. **Database Accuracy Fixes**: Correcting item IDs, passenger information, and payment method references to match actual database content

3. **Instruction Clarity Fixes**: Adding explicit behavior for edge cases, preventing ambiguous user responses, and specifying hidden information for testing agent reasoning

4. **Logical Consistency Fixes**: Correcting impossible scenarios (e.g., exchanging for identical items, cancelling already-departed flights)

5. **Evaluation Determinism Fixes**: Making instructions more specific to reduce evaluation ambiguity and ensure consistent grading

