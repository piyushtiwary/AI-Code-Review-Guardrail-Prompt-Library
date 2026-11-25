SHIP-42: Reroute a shipment

Scenario: A shipment has breached its SLA
  Given the shipment has passed its promised delivery time
  And an alternate carrier has capacity
  When the dispatcher requests a reroute
  And confirms the alternate carrier
  Then the shipment is assigned to the alternate carrier
  And the customer receives a revised delivery time

Scenario: A shipment is on time
  Given the shipment is within its promised delivery window
  When the dispatcher requests a reroute
  Then the shipment stays on its original route
