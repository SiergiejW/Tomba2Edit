# NPC and object handlers that touch items

Static scan of each handler's reachable code (see `game/item_scan.py`). "checks" is a read of the inventory table: a test or a display.

## A00

- `0x80115CB0` f_SpawnFishermensTownActorsFromPlacementTable - checks: 36 Star-shaped Cog
- `0x80116F64` f_HandleKainenHouseQuestItemActor - reacts to Tomba using: 99 Water Bucket; gives: 40 Bucket, 97 Bucket
- `0x80117228` (unnamed) - reacts to Tomba using: 99 Water Bucket; gives: 40 Bucket, 41 Crab Basket, 97 Bucket; item routines: f_HandleKainenHouseQuestItemActor
- `0x80117658` f_HandleRareFishOrBlueBucketPickup - gives: 40 Bucket, 58 Rare Fish
- `0x80118240` f_HandleBeachQuestPickup - checks: 69 Gold Fishhook; gives: 36 Star-shaped Cog, 69 Gold Fishhook, 83 Spoon, 120 Dried Fish
- `0x80119A9C` f_HandleGoldenCrabDialogueFirstVisit - takes: 42 Golden Crab
- `0x80119BD4` f_HandleGoldenCrabDialogueRepeatVisit - takes: 42 Golden Crab
- `0x8011A600` f_HandleWinConversationState - takes: 42 Golden Crab
- `0x8011A928` (unnamed) - takes: 42 Golden Crab
- `0x8011C164` f_HandleGoldenCrabAndWindmillActor - gives: 42 Golden Crab
- `0x8011C674` f_HandleNetBridgeRaiseNpcInteraction - checks: 36 Star-shaped Cog
- `0x8011CBD0` (unnamed) - checks: 36 Star-shaped Cog, 119 Small Fish
- `0x8011D108` f_HandleKainenInteraction - checks: 99 Water Bucket
- `0x8011D578` f_HandleKainenNpcActor - checks: 99 Water Bucket
- `0x80120F3C` f_HandleFeedTheChicksController - checks: 56 Red Chick, 57 Blue Chick, 77 Chick Food, 84 Nishiki's Wings; takes: 56 Red Chick, 57 Blue Chick, 77 Chick Food, 84 Nishiki's Wings; item routines: f_DeliverChickItemToNest, f_HandleFeedChicksInventoryUse
- `0x80121978` (unnamed) - checks: 69 Gold Fishhook
- `0x801220FC` f_HandleStuckFishhookQuestNpc - checks: 69 Gold Fishhook
- `0x80124E74` f_HandleSmallFishActor - gives: 119 Small Fish
- `0x80126C00` f_HandleFeedChicksInventoryUse - checks: 56 Red Chick, 57 Blue Chick, 77 Chick Food, 84 Nishiki's Wings; takes: 56 Red Chick, 57 Blue Chick, 77 Chick Food, 84 Nishiki's Wings; item routines: f_DeliverChickItemToNest
- `0x80126F9C` f_UpdateNestChickQuestState - checks: 84 Nishiki's Wings
- `0x80128308` f_InitializeRareFishOrBucketController - checks: 58 Rare Fish
- `0x80128760` f_HandleRareFishOrBucketSuspension - checks: 58 Rare Fish
- `0x8012DA04` f_HandleTownQuestRewardVisualActor - gives: 14 Bird Clothes, 29 Red Key, 35 Mermaid Scale
- `0x8013A330` f_HandleDryingRackController - checks: 36 Star-shaped Cog

## A01

- `0x80112100` f_UpdatePipeAreaQuestPickupActor - gives: 43 Bombs, 62 Anemone's Hot Dregs, 127 Mudball
- `0x801156A4` f_UpdatePipeAreaScenePropActor - gives: 28 Last Pig Bag
- `0x8011F7CC` (unnamed) - checks: 43 Bombs
- `0x8011FD84` f_HandleThirstyMinerInteraction - reacts to Tomba using: 98 Mermaid Bucket, 99 Water Bucket, 101 Holy Water Bucket, 160 Harp's Right Side; spawns whatever item Tomba used as a pickup (any item)
- `0x80120308` (unnamed) - reacts to Tomba using: 98 Mermaid Bucket, 99 Water Bucket, 101 Holy Water Bucket, 160 Harp's Right Side; spawns whatever item Tomba used as a pickup (any item)

## A02

- `0x80119880` FUN_A02__80119880 - reacts to Tomba using: 146 Small Mud Clump; reacts to Tomba using items 121-133: 121 Big Piece of Meat, 122 Underdone Meat, 123 Big Steak, 124 Potato, 125 1/2-Boiled Potato, 126 Boiled Potato, 127 Mudball, 128 Mudball Surprise …; reacts to Tomba using items 127-128: 127 Mudball, 128 Mudball Surprise; spawns whatever item Tomba used as a pickup (any item)
- `0x8011C52C` (unnamed) - checks: 73 Baked Banana; gives: 133 Banana; spawns whatever item Tomba used as a pickup (any item)
- `0x8011CBD4` f_AdvanceStarvingCharlesDialogue - checks: 73 Baked Banana
- `0x801213AC` f_UpdateGranActor - page rows use: 44 Trolley Rail
- `0x80122030` FUN_A02__80122030 - reacts to Tomba using items 119-126: 119 Small Fish, 120 Dried Fish, 121 Big Piece of Meat, 122 Underdone Meat, 123 Big Steak, 124 Potato, 125 1/2-Boiled Potato, 126 Boiled Potato; spawns whatever item Tomba used as a pickup (any item)
- `0x80123BB8` FUN_A02__80123bb8 - reacts to Tomba using: 127 Mudball, 128 Mudball Surprise, 146 Small Mud Clump; checks: 74 Clay, 75 Clay Spatula; spawns whatever item Tomba used as a pickup (any item)

## A04

- `0x80118A8C` f_UpdateKujaraQuestPickupActor - gives: 63 Anemone's Icy Dregs, 82 Rucksack, 148 Large Flower Seed
- `0x8011F700` f_SpawnKujaraRanchPlacementReward - checks: 45 Big Sack
- `0x8011FD54` f_UpdateKujaraRanchPlacementRewardActor - checks: 45 Big Sack
- `0x8012A180` FUN_A04__8012a180 - checks: 47 Traveler's Diary; page rows use: 30 Green Key, 47 Traveler's Diary
- `0x8012FDEC` FUN_A04__8012fdec - checks: 7 Fire Hammer, 15 Squirrel Clothes
- `0x801365DC` f_UpdateSantaBringTheBigSackConversation - checks: 45 Big Sack; page rows use: 7 Fire Hammer
- `0x801368A0` (unnamed) - checks: 45 Big Sack; page rows use: 7 Fire Hammer
- `0x80136CB8` FUN_A04__80136cb8 - checks: 46 Mountain Peach
- `0x80139614` FUN_A04__80139614 - checks: 15 Squirrel Clothes
- `0x80139D8C` FUN_A04__80139d8c - checks: 148 Large Flower Seed, 149 Petit-Face Flower
- `0x8013A294` FUN_A04__8013a294 - checks: 148 Large Flower Seed, 149 Petit-Face Flower; takes: 149 Petit-Face Flower
- `0x8013A8E4` (unnamed) - gives: 157 Mystery Reflector

## A05

- `0x801128F0` f_UpdateKujaraSummitQuestPickupActor - gives: 78 Large Fuse
- `0x80113D24` FUN_A05__80113d24 - spawns whatever item Tomba used as a pickup (any item)
- `0x80114354` f_UpdateKujaraHotSpringActor - checks: 100 Hot Water Bucket
- `0x80115198` (unnamed) - checks: 58 Rare Fish, 59 Rare Squid, 60 Rare Crab, 61 Rare Shrimp; page rows use: 58 Rare Fish, 59 Rare Squid, 60 Rare Crab, 61 Rare Shrimp
- `0x80118574` f_UpdateWhosTheLiarSisterActorA - page rows use: 135 Badge of Liars
- `0x80118DBC` f_UpdateWhosTheLiarSisterActorB - page rows use: 135 Badge of Liars
- `0x80119490` f_UpdateWhosTheLiarSisterActorC - checks: 114 1/2-Spell of Strength; page rows use: 135 Badge of Liars
- `0x8011FF44` FUN_A05__8011ff44 - checks: 37 Hexagon Gear, 107 Normal Fruit; page rows use: 37 Hexagon Gear
- `0x80121FF4` f_UpdateKujaraWashingExpertActor - checks: 78 Large Fuse; page rows use: 78 Large Fuse, 105 Toradako Oil Remover, 155 Golden Moon
- `0x8012809C` FUN_A05__8012809c - checks: 37 Hexagon Gear
- `0x8012B210` f_UpdateIceBlockAndSculptureActor - gives: 136 Ice Statue

## A06

- `0x801189E8` f_UpdateDonglinInteriorQuestObjectActor - checks: 80 Snow Firefly Box; gives: 25 Ghost Pig Bag, 167 Donglin Bell; takes: 49 Rock Crab
- `0x8011D520` FUN_A06__8011d520 - checks: 48 Blue Fruit
- `0x80122038` f_UpdateGhostStickerQuestActor - checks: 108 Clear Fruit
- `0x801251E8` FUN_A06__801251e8 - checks: 1 Grapple
- `0x80128A70` (unnamed) - spawns whatever item Tomba used as a pickup (any item)
- `0x8012B14C` FUN_A06__8012b14c - checks: 33 Cold Powder, 34 Hot Powder, 86 Invisible Mirror
- `0x8012E194` (unnamed) - gives: 49 Rock Crab
- `0x80137C9C` (unnamed) - checks: 48 Blue Fruit; page rows use: 31 Blue Key
- `0x8013AA14` FUN_A06__8013aa14 - checks: 49 Rock Crab, 79 Light Cutting Box, 80 Snow Firefly Box, 117 Snow Firefly
- `0x80140E4C` f_UpdateDonglinSnowFireflyPickup - gives: 117 Snow Firefly

## A07

- `0x80119570` f_UpdateCircusGearMechanismActor - checks: 38 Triangle Gear
- `0x8011B7CC` f_UpdateCircusClothesLockerActor - checks: 51 Closet Key; takes: 51 Closet Key
- `0x8011BAB4` FUN_A07__8011bab4 - checks: 51 Closet Key; takes: 51 Closet Key
- `0x8011D9B4` FUN_A07__8011d9b4 - reacts to Tomba using items 89-104: 89 Healthy Lunch, 90 Special Lunch, 91 Large Lunch Box, 92 Lunch Box, 93 Magic Juice, 94 Magic Water, 95 Baron, 96 Magic Wings …; spawns whatever item Tomba used as a pickup (any item)
- `0x8011E13C` FUN_A07__8011e13c - checks: 81 Getwell Plant
- `0x8011EB04` FUN_A07__8011eb04 - checks: 54 Seeds of Strength
- `0x801264C8` f_UpdateCircusPaonAtWellActor - checks: 50 Paon Grass; takes: 50 Paon Grass
- `0x80126BD8` FUN_A07__80126bd8 - checks: 50 Paon Grass; takes: 50 Paon Grass
- `0x80127690` (unnamed) - checks: 16 Pig Suit, 17 Swimming Pig Suit
- `0x80129900` FUN_A07__80129900 - checks: 50 Paon Grass
- `0x8012A018` f_UpdateCircusIceCandyAndTinyPigActor - checks: 118 Ice Candy; reacts to Tomba using items 64-68: 64 Tiny Flying Pig, 65 Tiny Flame Pig, 66 Tiny Ice Pig, 67 Tiny Ghost Pig, 68 Tiny Water Pig; spawns whatever item Tomba used as a pickup (any item); item routines: f_UpdatePigBallItemInteraction
- `0x8012A520` f_UpdateIceCandyAndTinyPigRewardSequence - checks: 118 Ice Candy

## A08

- `0x80114CF8` f_SpawnWaterTempleSceneActorsFromPlacementTable - checks: 39 Round Cog
- `0x80115D10` f_UpdateWaterTempleQuestItemPickupActor - gives: 39 Round Cog, 158 Crystal
- `0x80121C2C` FUN_A08__80121c2c - reacts to Tomba using: 136 Ice Statue
- `0x80122544` FUN_A08__80122544 - checks: 53 Pig Nose Panel; takes: 53 Pig Nose Panel
- `0x801280D0` f_UpdateWaterTempleQuestPropActor - checks: 110 Taboo Fruit; gives: 84 Nishiki's Wings; takes: 159 Crystal Panel
- `0x8012B97C` f_UpdateLastEvilPigAccessActor - checks: 29 Red Key
- `0x8012BC44` FUN_A08__8012bc44 - page rows use: 152 Minitta Bell
- `0x8012CA40` (unnamed) - checks: 82 Rucksack; reacts to Tomba using items 89-104: 89 Healthy Lunch, 90 Special Lunch, 91 Large Lunch Box, 92 Lunch Box, 93 Magic Juice, 94 Magic Water, 95 Baron, 96 Magic Wings …; spawns whatever item Tomba used as a pickup (any item)
- `0x8012D610` f_UpdateRareCrabPickupActor - gives: 60 Rare Crab
- `0x80130070` f_UpdateClosedClamshellQuestActor - checks: 35 Mermaid Scale
- `0x80132228` f_UpdateGiantFishActor - checks: 159 Crystal Panel, 164 Mermaid's Harp
- `0x80139710` f_UpdateWaterTempleMermaidAndGearNpcActor - checks: 36 Star-shaped Cog, 37 Hexagon Gear, 38 Triangle Gear, 39 Round Cog; page rows use: 36 Star-shaped Cog, 37 Hexagon Gear, 38 Triangle Gear, 39 Round Cog
- `0x8013A9F8` FUN_A08__8013a9f8 - checks: 80 Snow Firefly Box
- `0x8013BCAC` FUN_A08__8013bcac - checks: 157 Mystery Reflector
- `0x8013C6B4` (unnamed) - checks: 158 Crystal; page rows use: 154 Old Man's Proof, 158 Crystal, 159 Crystal Panel, 165 100-Year Amulet
- `0x8013C8A8` (unnamed) - checks: 165 100-Year Amulet
- `0x80140E10` (unnamed) - gives: 27 Water Pig Bag
- `0x80145970` (unnamed) - checks: 157 Mystery Reflector
- `0x80146F60` (unnamed) - checks: 80 Snow Firefly Box, 165 100-Year Amulet; page rows use: 154 Old Man's Proof, 158 Crystal, 159 Crystal Panel, 165 100-Year Amulet
- `0x80146F6C` (unnamed) - checks: 80 Snow Firefly Box, 165 100-Year Amulet; page rows use: 154 Old Man's Proof, 158 Crystal, 159 Crystal Panel, 165 100-Year Amulet

## A0J

- `0x8010BE40` f_UpdateGoldenTowerPigElderActor - checks: 2 Doka Pin, 5 Glacier Boomerang, 8 Torch Hammer, 55 Golden Sun, 155 Golden Moon, 156 Golden Star
- `0x8010CA1C` (unnamed) - checks: 88 Golden Powder

## A0K

- `0x8011187C` f_UpdateTinyMouseVillageRewardActor - gives: 109 Minitta Mushroom, 156 Golden Star
- `0x80114408` f_UpdateBerryNutsHostDialogue - checks: 83 Spoon
- `0x80114994` f_UpdateTinyMouseVillageNpcActor - checks: 83 Spoon

