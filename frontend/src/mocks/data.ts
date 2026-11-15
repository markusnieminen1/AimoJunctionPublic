// Synthetic mock fixtures for offline MSW mode (VITE_USE_MOCKS=true).
// Zero proprietary supplier records, GTINs, or vendor descriptions are bundled here:
// all 12 items below are fictional foodservice products exercising every contract field & policy rule.
// 100001 €1.01 Stock · 100002 €1.65 UHT · 100003 384 in stock · 100008 Terminal, cut-off passed · 100009 priced, not deliverable.
import type { DemoPersona, Product } from "../api/types";

export const SYNTHETIC_PRODUCTS: Product[] = [
  {
    id: "100001",
    name: "Organic Lactose-Free Semi-Skimmed Milk 1 L",
    names: {
      en: "Organic Lactose-Free Semi-Skimmed Milk 1 L",
      fi: "Luomu laktoositon kevytmaito 1 L",
    },
    brand: "Nordic Dairy",
    vendor: "Nordic Dairy Co.",
    category: "10400",
    category_name: "Milk & Dairy Drinks",
    sales_unit: "ST",
    base_unit: "L",
    units: [{ unit_id: "ST", size_in_base_units: 1 }],
    temperature_condition: 4,
    country_of_origin: "FI",
    allergens: ["AM"],
    non_allergens: ["ML", "AW"],
    labels: ["FREE_FROM_LACTOSE", "FREE_FROM_GLUTEN"],
    co2_kg_per_kg: 1.15,
    delivery_reliability: 0.96,
    image_url: null,
  },
  {
    id: "100002",
    name: "Lactose-Free UHT Semi-Skimmed Milk 1 L",
    names: {
      en: "Lactose-Free UHT Semi-Skimmed Milk 1 L",
      fi: "Laktoositon UHT kevytmaitojuoma 1 L",
    },
    brand: "Nordic Dairy",
    vendor: "Nordic Dairy Co.",
    category: "10400",
    category_name: "Milk & Dairy Drinks",
    sales_unit: "ST",
    base_unit: "L",
    units: [{ unit_id: "ST", size_in_base_units: 1 }],
    temperature_condition: 8,
    country_of_origin: "FI",
    allergens: ["AM"],
    non_allergens: ["ML", "AW"],
    labels: ["FREE_FROM_LACTOSE", "FREE_FROM_GLUTEN"],
    co2_kg_per_kg: 1.2,
    delivery_reliability: 0.95,
    image_url: null,
  },
  {
    id: "100003",
    name: "Lactose-Free Catering Milk Drink 1 L",
    names: {
      en: "Lactose-Free Catering Milk Drink 1 L",
      fi: "Laktoositon maitojuoma suurkeittiö 1 L",
    },
    brand: "Fjord Dairy",
    vendor: "Fjord Foods Oy",
    category: "10400",
    category_name: "Milk & Dairy Drinks",
    sales_unit: "ST",
    base_unit: "L",
    units: [{ unit_id: "ST", size_in_base_units: 1 }],
    temperature_condition: 4,
    country_of_origin: "SE",
    allergens: ["AM"],
    non_allergens: ["ML", "AW"],
    labels: ["FREE_FROM_LACTOSE", "FREE_FROM_GLUTEN"],
    co2_kg_per_kg: 1.18,
    delivery_reliability: 0.92,
    image_url: null,
  },
  {
    id: "100004",
    name: "Lactose-Free Whole Milk 1 L",
    names: {
      en: "Lactose-Free Whole Milk 1 L",
      fi: "Laktoositon täysmaito 1 L",
    },
    brand: "Nordic Dairy",
    vendor: "Nordic Dairy Co.",
    category: "10400",
    category_name: "Milk & Dairy Drinks",
    sales_unit: "ST",
    base_unit: "L",
    units: [{ unit_id: "ST", size_in_base_units: 1 }],
    temperature_condition: 4,
    country_of_origin: "FI",
    allergens: ["AM"],
    non_allergens: ["ML", "AW"],
    labels: ["FREE_FROM_LACTOSE", "FREE_FROM_GLUTEN"],
    co2_kg_per_kg: 1.25,
    delivery_reliability: 0.94,
    image_url: null,
  },
  {
    id: "100005",
    name: "Oat Barista Drink 1 L",
    names: {
      en: "Oat Barista Drink 1 L",
      fi: "Kaurajuoma Barista 1 L",
    },
    brand: "Nordic Oat",
    vendor: "Nordic Harvest Oy",
    category: "10413",
    category_name: "Plant-Based Drinks",
    sales_unit: "ST",
    base_unit: "L",
    units: [{ unit_id: "ST", size_in_base_units: 1 }],
    temperature_condition: 8,
    country_of_origin: "FI",
    allergens: ["GO"],
    non_allergens: ["AM", "ML", "AW"],
    labels: ["VEGAN", "FREE_FROM_MILK", "FREE_FROM_LACTOSE", "FREE_FROM_GLUTEN", "HEART_SYMBOL"],
    co2_kg_per_kg: 0.42,
    delivery_reliability: 0.97,
    image_url: null,
  },
  {
    id: "100006",
    name: "Lactose-Free Culinary Cream 15% 5 L",
    names: {
      en: "Lactose-Free Culinary Cream 15% 5 L",
      fi: "Laktoositon ruokakerma 15% 5 L",
    },
    brand: "Nordic Dairy",
    vendor: "Nordic Dairy Co.",
    category: "10405",
    category_name: "Creams & Culinary Dairy",
    sales_unit: "ST",
    base_unit: "L",
    units: [{ unit_id: "ST", size_in_base_units: 5 }],
    temperature_condition: 4,
    country_of_origin: "FI",
    allergens: ["AM"],
    non_allergens: ["ML", "AW"],
    labels: ["FREE_FROM_LACTOSE", "FREE_FROM_GLUTEN"],
    co2_kg_per_kg: 2.4,
    delivery_reliability: 0.96,
    image_url: null,
  },
  {
    id: "100007",
    name: "Gluten-Free Dark Seed Loaf 500 g",
    names: {
      en: "Gluten-Free Dark Seed Loaf 500 g",
      fi: "Gluteeniton tumma siemenleipä 500 g",
    },
    brand: "Artisan Bakehouse",
    vendor: "Artisan Bakehouse Oy",
    category: "10701",
    category_name: "Bakery & Bread",
    sales_unit: "ST",
    base_unit: "KG",
    units: [{ unit_id: "ST", size_in_base_units: 0.5 }],
    temperature_condition: 8,
    country_of_origin: "FI",
    allergens: ["AE", "AY:MAY_CONTAIN"],
    non_allergens: ["AW", "AM", "ML"],
    labels: ["FREE_FROM_GLUTEN", "FREE_FROM_LACTOSE", "FREE_FROM_MILK"],
    co2_kg_per_kg: 0.85,
    delivery_reliability: 0.95,
    image_url: null,
  },
  {
    id: "100008",
    name: "Fresh Atlantic Salmon Fillet Case",
    names: {
      en: "Fresh Atlantic Salmon Fillet Case",
      fi: "Tuore lohifilee laatikko",
    },
    brand: "Archipelago Catch",
    vendor: "Archipelago Seafood Oy",
    category: "10201",
    category_name: "Fresh Fish & Seafood",
    sales_unit: "KI",
    base_unit: "ST",
    units: [
      { unit_id: "KI", size_in_base_units: 2 },
      { unit_id: "ST", size_in_base_units: 1 },
    ],
    temperature_condition: 1,
    country_of_origin: "NO",
    allergens: ["AF"],
    non_allergens: ["AM", "ML", "AW"],
    labels: ["FREE_FROM_LACTOSE", "FREE_FROM_MILK", "FREE_FROM_GLUTEN"],
    co2_kg_per_kg: 4.8,
    delivery_reliability: 0.86,
    image_url: null,
  },
  {
    id: "100009",
    name: "Heirloom Beefsteak Tomatoes 5 kg",
    names: {
      en: "Heirloom Beefsteak Tomatoes 5 kg",
      fi: "Pihvitomaatti 5 kg",
    },
    brand: "Nordic Greens",
    vendor: "Greenhouse Growers Oy",
    category: "10501",
    category_name: "Fresh Vegetables",
    sales_unit: "LA",
    base_unit: "KG",
    units: [{ unit_id: "LA", size_in_base_units: 5 }],
    temperature_condition: 5,
    country_of_origin: "FI",
    allergens: [],
    non_allergens: [],
    labels: [],
    co2_kg_per_kg: 0.65,
    delivery_reliability: 0.75,
    image_url: null,
  },
  {
    id: "100010",
    name: "Organic Rolled Porridge Oats 2.5 kg",
    names: {
      en: "Organic Rolled Porridge Oats 2.5 kg",
      fi: "Luomu kaurahiutale 2,5 kg",
    },
    brand: "Nordic Mill",
    vendor: "Nordic Mill Oy",
    category: "10801",
    category_name: "Dry Goods & Grains",
    sales_unit: "ST",
    base_unit: "KG",
    units: [{ unit_id: "ST", size_in_base_units: 2.5 }],
    temperature_condition: 8,
    country_of_origin: "FI",
    allergens: [],
    non_allergens: ["AW", "AM", "ML"],
    labels: ["VEGAN", "FREE_FROM_GLUTEN", "FREE_FROM_MILK", "FREE_FROM_LACTOSE", "HEART_SYMBOL"],
    co2_kg_per_kg: 0.55,
    delivery_reliability: 0.98,
    image_url: null,
  },
  {
    id: "100011",
    name: "Cold-Pressed Rapeseed Culinary Oil 5 L",
    names: {
      en: "Cold-Pressed Rapeseed Culinary Oil 5 L",
      fi: "Kylmäpuristettu rypsiöljy 5 L",
    },
    brand: "Boreal Kitchen",
    vendor: "Boreal Oils Oy",
    category: "10805",
    category_name: "Oils & Condiments",
    sales_unit: "ST",
    base_unit: "L",
    units: [{ unit_id: "ST", size_in_base_units: 5 }],
    temperature_condition: 8,
    country_of_origin: "FI",
    allergens: [],
    non_allergens: ["AW", "AM", "ML", "AE"],
    labels: ["VEGAN", "FREE_FROM_GLUTEN", "FREE_FROM_MILK", "FREE_FROM_LACTOSE", "HEART_SYMBOL"],
    co2_kg_per_kg: 1.6,
    delivery_reliability: 0.97,
    image_url: null,
  },
  {
    id: "100012",
    name: "Frozen Wild Lingonberries 2.5 kg",
    names: {
      en: "Frozen Wild Lingonberries 2.5 kg",
      fi: "Kotimainen puolukka pakaste 2,5 kg",
    },
    brand: "Arctic Berry",
    vendor: "Arctic Berry Co.",
    category: "10601",
    category_name: "Frozen Berries & Fruit",
    sales_unit: "ST",
    base_unit: "KG",
    units: [{ unit_id: "ST", size_in_base_units: 2.5 }],
    temperature_condition: 7,
    country_of_origin: "FI",
    allergens: [],
    non_allergens: ["AW", "AM", "ML"],
    labels: ["VEGAN", "FREE_FROM_GLUTEN", "FREE_FROM_MILK", "FREE_FROM_LACTOSE"],
    co2_kg_per_kg: 0.45,
    delivery_reliability: 0.96,
    image_url: null,
  },
];

export const PRODUCTS: Product[] = SYNTHETIC_PRODUCTS;

export const productById = (id: string): Product | undefined => PRODUCTS.find((p) => p.id === id);

export type MockSupply = {
  /** catalogPrice excl. VAT per sales unit; null = NO_PRICE */
  price: number | null;
  /** optional listPrice excl. VAT when product is on sale (listPrice > price) */
  listPrice?: number;
  model: "Stock" | "Terminal";
  qty: "UNLIMITED" | "FINITE" | "TERMINAL_UNKNOWN" | "ZERO";
  amount?: number;
  /** Terminal item whose supplier cut-off has already passed → LATE + CUTOFF_PASSED + SUPPLIER_QTY_UNKNOWN */
  cutoffPassed?: boolean;
};

export const SUPPLY: Record<string, MockSupply> = {
  "100001": { price: 1.01, listPrice: 1.25, model: "Stock", qty: "UNLIMITED" },
  "100002": { price: 1.65, listPrice: 1.95, model: "Stock", qty: "UNLIMITED" },
  "100003": { price: 1.19, model: "Stock", qty: "FINITE", amount: 384 },
  "100004": { price: 5.05, model: "Stock", qty: "UNLIMITED" },
  "100005": { price: 1.85, listPrice: 2.15, model: "Stock", qty: "UNLIMITED" },
  "100006": { price: 42.0, listPrice: 49.5, model: "Stock", qty: "UNLIMITED" },
  "100007": { price: 14.5, listPrice: 16.5, model: "Stock", qty: "UNLIMITED" },
  "100008": { price: 13.9, model: "Terminal", qty: "TERMINAL_UNKNOWN", cutoffPassed: true },
  "100009": { price: 6.8, model: "Stock", qty: "ZERO" },
  "100010": { price: 2.25, model: "Stock", qty: "UNLIMITED" },
  "100011": { price: 3.3, listPrice: 3.95, model: "Stock", qty: "UNLIMITED" },
  "100012": { price: 1.95, listPrice: 2.35, model: "Stock", qty: "UNLIMITED" },
};

export const VAT_FOOD_PERCENT = 13.5;

// ---------- Seed identities ----------

export const SEED_COMPANY = { id: "cmp_aurora", name: "Hotel Aurora Oy" };

export const SEED_USER = {
  id: "usr_anna",
  name: "Anna Korhonen",
  email: "anna.korhonen@hotel.fi",
  password: "demo1234",
  company_id: SEED_COMPANY.id,
};

/** Agent secrets are fixed in mock mode so tests and curl examples work. Never real secrets. */
export const SEED_AGENT_SECRETS = {
  agt_kitchen01: "aks_demo_kitchen_reorder_bot",
  agt_scout01: "aks_demo_readonly_scout",
  agt_ward01: "aks_demo_ward_diet_bot",
} as const;

/** Generic reference kitchen personas for local demo and offline mock mode. */
export const SEED_DEMO_PERSONAS: DemoPersona[] = [
  {
    email: "anna.korhonen@hotel.fi",
    name: "Anna Korhonen",
    company_name: "Hotel Aurora Oy",
    group: "demo",
    role_label: "Hotel F&B Buyer",
  },
  {
    email: "mikko.laine@restaurant.fi",
    name: "Mikko Laine",
    company_name: "Bistro Satama Oy",
    group: "demo",
    role_label: "Head Chef",
  },
  {
    email: "sari.virtanen@festival.fi",
    name: "Sari Virtanen",
    company_name: "Nordic Campus Catering Oy",
    group: "demo",
    role_label: "Event Producer",
  },
];

export const SEED_EXTRA_TENANTS = [
  {
    company: { id: "cmp_bistro", name: "Bistro Satama Oy", business_id: "2345678-9" },
    user: { id: "usr_mikko", name: "Mikko Laine", email: "mikko.laine@restaurant.fi" },
  },
  {
    company: { id: "cmp_campus", name: "Nordic Campus Catering Oy", business_id: "3456789-0" },
    user: { id: "usr_sari", name: "Sari Virtanen", email: "sari.virtanen@festival.fi" },
  },
] as const;
