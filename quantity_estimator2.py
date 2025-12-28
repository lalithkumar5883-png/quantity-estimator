"""
Professional Quantity Estimator (Streamlit) - Enhanced Version

Save as: professional_quantity_estimator.py
Run: streamlit run professional_quantity_estimator.py

Features:
 - OCR-based dimension extraction from floor plans
 - Multi-floor support with individual floor configurations
 - Comprehensive material estimation (concrete, masonry, steel, finishes)
 - Cost estimation with customizable material rates
 - PDF/Excel report generation
 - Project save/load functionality
 - Visual analytics and charts
 - Professional BOQ output format
"""

from __future__ import annotations
import os
import pytesseract
import streamlit as st

if os.name == "nt":  # Windows only
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
from PIL import Image, ImageFilter, ImageOps
import pytesseract


from PIL import Image
import numpy as np
import pytesseract
import re

import pandas as pd
import io
import json
import base64
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Tuple, Any
from enum import Enum
from datetime import datetime
import plotly.express as px
import plotly.graph_objects as go
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
def preprocess_image(image: Image.Image) -> Image.Image:
    # Convert to grayscale
    gray = ImageOps.grayscale(image)

    # Improve contrast
    gray = ImageOps.autocontrast(gray)

    # Slight sharpening
    gray = gray.filter(ImageFilter.SHARPEN)

    return gray

# ============================================================================
# CONFIGURATION & CONSTANTS
# ============================================================================

class UnitSystem(Enum):
    METRIC = "metric"
    IMPERIAL = "imperial"

@dataclass
class MaterialRates:
    """Default material rates (can be customized by user)"""
    cement_per_bag: float = 350.0  # INR per 50kg bag
    sand_per_m3: float = 1500.0
    aggregate_per_m3: float = 1800.0
    brick_per_unit: float = 8.0
    steel_per_kg: float = 70.0
    water_per_liter: float = 0.05
    labor_rate_per_day: float = 800.0
    currency: str = "INR"

@dataclass
class ConcreteGrade:
    """Concrete grade specifications"""
    name: str
    mix_ratio: Tuple[float, float, float]  # cement:sand:aggregate
    water_cement_ratio: float
    strength_mpa: float

CONCRETE_GRADES: Dict[str, ConcreteGrade] = {
    "M15": ConcreteGrade("M15", (1, 2, 4), 0.60, 15),
    "M20": ConcreteGrade("M20", (1, 1.5, 3), 0.55, 20),
    "M25": ConcreteGrade("M25", (1, 1, 2), 0.50, 25),
    "M30": ConcreteGrade("M30", (1, 0.75, 1.5), 0.45, 30),
}

@dataclass
class BrickType:
    """Brick specifications"""
    name: str
    length_mm: float
    width_mm: float
    height_mm: float
    bricks_per_m3: int
    
BRICK_TYPES: Dict[str, BrickType] = {
    "Standard (230x110x75mm)": BrickType("Standard", 230, 110, 75, 500),
    "Modular (190x90x90mm)": BrickType("Modular", 190, 90, 90, 588),
    "Jumbo (230x110x150mm)": BrickType("Jumbo", 230, 110, 150, 250),
}

# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class Room:
    """Represents a single room/space"""
    id: str
    label: str
    length_m: float
    width_m: float
    height_m: float = 3.0
    floor_number: int = 0
    room_type: str = "General"
    
    @property
    def area_m2(self) -> float:
        return self.length_m * self.width_m
    
    @property
    def area_ft2(self) -> float:
        return self.area_m2 * 10.7639
    
    @property
    def volume_m3(self) -> float:
        return self.area_m2 * self.height_m
    
    @property
    def perimeter_m(self) -> float:
        return 2 * (self.length_m + self.width_m)

@dataclass
class BuildingElement:
    """Represents a structural element"""
    name: str
    element_type: str  # slab, beam, column, footing, wall
    quantity: int
    length_m: float
    width_m: float
    depth_m: float
    concrete_grade: str = "M20"
    steel_percentage: float = 1.0
    
    @property
    def volume_m3(self) -> float:
        return self.length_m * self.width_m * self.depth_m * self.quantity

@dataclass
class Floor:
    """Represents a building floor"""
    floor_number: int
    name: str
    rooms: List[Room] = field(default_factory=list)
    slab_thickness_m: float = 0.12
    floor_height_m: float = 3.0
    
    @property
    def total_area_m2(self) -> float:
        return sum(room.area_m2 for room in self.rooms)

@dataclass
class Project:
    """Main project container"""
    name: str
    created_date: str
    floors: List[Floor] = field(default_factory=list)
    building_elements: List[BuildingElement] = field(default_factory=list)
    material_rates: MaterialRates = field(default_factory=MaterialRates)
    notes: str = ""
    client_name: str = ""
    project_location: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "created_date": self.created_date,
            "client_name": self.client_name,
            "project_location": self.project_location,
            "notes": self.notes,
            "floors": [
                {
                    "floor_number": f.floor_number,
                    "name": f.name,
                    "slab_thickness_m": f.slab_thickness_m,
                    "floor_height_m": f.floor_height_m,
                    "rooms": [asdict(r) for r in f.rooms]
                }
                for f in self.floors
            ],
            "building_elements": [asdict(e) for e in self.building_elements],
            "material_rates": asdict(self.material_rates)
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Project":
        project = cls(
            name=data["name"],
            created_date=data["created_date"],
            client_name=data.get("client_name", ""),
            project_location=data.get("project_location", ""),
            notes=data.get("notes", "")
        )
        project.material_rates = MaterialRates(**data.get("material_rates", {}))
        
        for floor_data in data.get("floors", []):
            floor = Floor(
                floor_number=floor_data["floor_number"],
                name=floor_data["name"],
                slab_thickness_m=floor_data.get("slab_thickness_m", 0.12),
                floor_height_m=floor_data.get("floor_height_m", 3.0)
            )
            for room_data in floor_data.get("rooms", []):
                floor.rooms.append(Room(**room_data))
            project.floors.append(floor)
        
        for elem_data in data.get("building_elements", []):
            project.building_elements.append(BuildingElement(**elem_data))
        
        return project

# ============================================================================
# IMAGE PROCESSING & OCR
# ============================================================================

class ImageProcessor:
    """Handles image preprocessing and OCR operations"""
    
    @staticmethod
    def preprocess_for_ocr(pil_img: Image.Image, 
                           denoise_strength: int = 9,
                           threshold_block_size: int = 35) -> np.ndarray:
        """Advanced preprocessing pipeline for OCR"""
        img = np.array(pil_img.convert("RGB"))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        
        # Contrast enhancement using CLAHE
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        
        # Bilateral filter for edge-preserving denoising
        gray = cv2.bilateralFilter(gray, denoise_strength, 75, 75)
        
        # Adaptive thresholding
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, threshold_block_size, 5
        )
        
        # Morphological operations to clean up
        kernel = np.ones((2, 2), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        
        return thresh
    
    @staticmethod
    def extract_text_with_confidence(pil_img: Image.Image) -> Tuple[str, List[Dict]]:
        """Extract text with confidence scores"""
        preprocessed = ImageProcessor.preprocess_for_ocr(pil_img)
        config = r'--oem 3 --psm 6'
        
        try:
            data = pytesseract.image_to_data(
                preprocessed, config=config, 
                output_type=pytesseract.Output.DICT
            )
            
            words_with_confidence = []
            text_parts = []
            
            for i, word in enumerate(data['text']):
                if word.strip():
                    conf = int(data['conf'][i])
                    words_with_confidence.append({
                        'word': word,
                        'confidence': conf,
                        'x': data['left'][i],
                        'y': data['top'][i],
                        'width': data['width'][i],
                        'height': data['height'][i]
                    })
                    text_parts.append(word)
            
            return " ".join(text_parts), words_with_confidence
            
        except Exception as e:
            st.error(f"OCR processing failed: {str(e)}")
            return "", []
    
    @staticmethod
    def detect_scale(text: str) -> Optional[Tuple[float, str]]:
        """Attempt to detect scale notation in the plan"""
        scale_patterns = [
            r'1\s*:\s*(\d+)',  # 1:100, 1:50
            r'scale\s*[=:]\s*1\s*:\s*(\d+)',
            r'(\d+)\s*mm\s*=\s*(\d+)\s*m',
        ]
        
        for pattern in scale_patterns:
            match = re.search(pattern, text.lower())
            if match:
                return float(match.group(1)), match.group(0)
        return None

def clean_ocr_text(text: str) -> str:
    replacements = {
        "’": "'",
        "‘": "'",
        "°": "'",
        "″": "'",
        "×": "x",
        "X": "x"
    }

    for k, v in replacements.items():
        text = text.replace(k, v)

    return text


class DimensionExtractor:
    """Extracts and parses dimensions from OCR text"""

    DIMENSION_PATTERNS = [
        r'(\d{1,5}(?:\.\d+)?)\s*[xX×]\s*(\d{1,5}(?:\.\d+)?)',
        r'(\d{1,5}(?:\.\d+)?)\s*[mM]\s*[xX×]\s*(\d{1,5}(?:\.\d+)?)\s*[mM]',
        r"(\d{1,5}(?:\.\d+)?)\s*['′]\s*[xX×]\s*(\d{1,5}(?:\.\d+)?)\s*['′]",
    ]

    UNIT_CONVERSIONS = {
        'mm': 0.001,
        'cm': 0.01,
        'm': 1.0,
        'ft': 0.3048,
        "'": 0.3048,
        'in': 0.0254,
        '"': 0.0254,
    }

    @classmethod
    def detect_unit(cls, text: str, position: int) -> Tuple[str, float]:
        """Detect unit from context around a match"""
        window = text[position:position + 15].lower()
        
        for unit, factor in cls.UNIT_CONVERSIONS.items():
            if unit in window:
                return unit, factor
        
        return 'm', 1.0  # Default to meters
    
    @classmethod
    def extract_dimensions(cls, text: str) -> List[Dict[str, Any]]:
        """Extract all dimension pairs from text"""
        dimensions = []
        
        for pattern in cls.DIMENSION_PATTERNS:
            for match in re.finditer(pattern, text):
                val1, val2 = float(match.group(1)), float(match.group(2))
                unit, factor = cls.detect_unit(text, match.end())
                
                # Convert to meters
                length_m = val1 * factor
                width_m = val2 * factor
                
                # Filter out unrealistic dimensions
                if 0.5 <= length_m <= 100 and 0.5 <= width_m <= 100:
                    dimensions.append({
                        'raw': match.group(0),
                        'length_m': round(length_m, 3),
                        'width_m': round(width_m, 3),
                        'area_m2': round(length_m * width_m, 3),
                        'unit_detected': unit,
                        'confidence': 'high' if unit != 'm' else 'medium'
                    })
        
        # Remove duplicates
        seen = set()
        unique_dims = []
        for d in dimensions:
            key = (d['length_m'], d['width_m'])
            if key not in seen:
                seen.add(key)
                unique_dims.append(d)
        
        return unique_dims

# ============================================================================
# MATERIAL CALCULATORS
# ============================================================================

class MaterialCalculator:
    """Comprehensive material quantity calculations"""
    
    @staticmethod
    def calculate_concrete_materials(
        volume_m3: float,
        grade: ConcreteGrade,
        cement_bag_volume: float = 0.0347,
        wastage_pct: float = 0.05
    ) -> Dict[str, float]:
        """Calculate concrete materials with dry volume factor"""
        
        # Dry volume factor (concrete shrinks ~52-54% when wet)
        dry_volume = volume_m3 * 1.54
        
        c, s, a = grade.mix_ratio
        total = c + s + a
        
        cement_vol = dry_volume * (c / total) * (1 + wastage_pct)
        sand_vol = dry_volume * (s / total) * (1 + wastage_pct * 2)
        agg_vol = dry_volume * (a / total) * (1 + wastage_pct)
        
        cement_bags = cement_vol / cement_bag_volume
        water_liters = cement_bags * 50 * grade.water_cement_ratio  # 50kg per bag
        
        return {
            'wet_volume_m3': volume_m3,
            'dry_volume_m3': dry_volume,
            'cement_bags': round(cement_bags, 2),
            'cement_kg': round(cement_bags * 50, 2),
            'cement_vol_m3': round(cement_vol, 3),
            'sand_m3': round(sand_vol, 3),
            'aggregate_m3': round(agg_vol, 3),
            'water_liters': round(water_liters, 2)
        }
    
    @staticmethod
    def calculate_masonry(
        wall_length_m: float,
        wall_height_m: float,
        wall_thickness_m: float,
        brick_type: BrickType,
        mortar_mix: Tuple[float, float] = (1, 6),
        opening_deduction_pct: float = 0.15,
        wastage_pct: float = 0.05
    ) -> Dict[str, float]:
        """Calculate masonry materials"""
        
        # Gross wall area and volume
        gross_area = wall_length_m * wall_height_m
        net_area = gross_area * (1 - opening_deduction_pct)
        wall_volume = net_area * wall_thickness_m
        
        # Bricks calculation
        bricks_needed = wall_volume * brick_type.bricks_per_m3 * (1 + wastage_pct)
        
        # Mortar calculation (typically 25-30% of wall volume)
        mortar_volume = wall_volume * 0.25
        c, s = mortar_mix
        total = c + s
        
        mortar_cement_vol = mortar_volume * (c / total) * 1.33  # Dry volume factor
        mortar_sand_vol = mortar_volume * (s / total) * 1.33
        
        cement_bags = mortar_cement_vol / 0.0347
        
        return {
            'gross_wall_area_m2': round(gross_area, 2),
            'net_wall_area_m2': round(net_area, 2),
            'wall_volume_m3': round(wall_volume, 3),
            'bricks_count': int(round(bricks_needed)),
            'mortar_volume_m3': round(mortar_volume, 3),
            'mortar_cement_bags': round(cement_bags, 2),
            'mortar_sand_m3': round(mortar_sand_vol, 3)
        }
    
    @staticmethod
    def calculate_steel_reinforcement(
        concrete_volume_m3: float,
        element_type: str,
        steel_percentage: Optional[float] = None
    ) -> Dict[str, float]:
        """Calculate steel reinforcement based on element type"""
        
        # Default steel percentages by element type
        default_percentages = {
            'slab': 0.8,
            'beam': 1.5,
            'column': 2.5,
            'footing': 0.5,
            'lintel': 1.0,
            'staircase': 1.2
        }
        
        pct = steel_percentage or default_percentages.get(element_type.lower(), 1.0)
        
        # Steel weight = volume × percentage × steel density (7850 kg/m³)
        steel_kg = concrete_volume_m3 * (pct / 100) * 7850
        
        return {
            'steel_percentage': pct,
            'steel_kg': round(steel_kg, 2),
            'steel_tonnes': round(steel_kg / 1000, 3)
        }
    
    @staticmethod
    def calculate_plastering(
        area_m2: float,
        thickness_mm: float = 12,
        plaster_mix: Tuple[float, float] = (1, 6),
        wastage_pct: float = 0.10
    ) -> Dict[str, float]:
        """Calculate plastering materials"""
        
        thickness_m = thickness_mm / 1000
        plaster_volume = area_m2 * thickness_m * (1 + wastage_pct)
        dry_volume = plaster_volume * 1.33
        
        c, s = plaster_mix
        total = c + s
        
        cement_vol = dry_volume * (c / total)
        sand_vol = dry_volume * (s / total)
        
        cement_bags = cement_vol / 0.0347
        
        return {
            'plaster_area_m2': round(area_m2, 2),
            'plaster_volume_m3': round(plaster_volume, 4),
            'cement_bags': round(cement_bags, 2),
            'sand_m3': round(sand_vol, 3)
        }
    
    @staticmethod
    def calculate_flooring(
        area_m2: float,
        tile_size_mm: Tuple[float, float] = (600, 600),
        wastage_pct: float = 0.10
    ) -> Dict[str, Any]:
        """Calculate flooring materials"""
        
        tile_area_m2 = (tile_size_mm[0] / 1000) * (tile_size_mm[1] / 1000)
        tiles_needed = (area_m2 / tile_area_m2) * (1 + wastage_pct)
        
        # Adhesive: approximately 4-5 kg per m²
        adhesive_kg = area_m2 * 4.5
        
        # Grout: approximately 0.5 kg per m²
        grout_kg = area_m2 * 0.5
        
        return {
            'floor_area_m2': round(area_m2, 2),
            'tiles_count': int(round(tiles_needed)),
            'tile_size': f"{int(tile_size_mm[0])}x{int(tile_size_mm[1])}mm",
            'adhesive_kg': round(adhesive_kg, 2),
            'grout_kg': round(grout_kg, 2)
        }

# ============================================================================
# COST ESTIMATOR
# ============================================================================

class CostEstimator:
    """Calculate material and labor costs"""
    
    @staticmethod
    def calculate_material_costs(
        materials: Dict[str, float],
        rates: MaterialRates
    ) -> Dict[str, float]:
        """Calculate costs based on material quantities and rates"""
        
        costs = {}
        
        if 'cement_bags' in materials:
            costs['cement_cost'] = materials['cement_bags'] * rates.cement_per_bag
        
        if 'sand_m3' in materials:
            costs['sand_cost'] = materials['sand_m3'] * rates.sand_per_m3
        
        if 'aggregate_m3' in materials:
            costs['aggregate_cost'] = materials['aggregate_m3'] * rates.aggregate_per_m3
        
        if 'bricks_count' in materials:
            costs['brick_cost'] = materials['bricks_count'] * rates.brick_per_unit
        
        if 'steel_kg' in materials:
            costs['steel_cost'] = materials['steel_kg'] * rates.steel_per_kg
        
        if 'water_liters' in materials:
            costs['water_cost'] = materials['water_liters'] * rates.water_per_liter
        
        costs['total_material_cost'] = sum(costs.values())
        
        return {k: round(v, 2) for k, v in costs.items()}
    
    @staticmethod
    def estimate_labor_cost(
        work_type: str,
        quantity: float,
        labor_rate: float
    ) -> float:
        """Estimate labor cost based on work type"""
        
        # Labor productivity rates (quantity per worker per day)
        productivity = {
            'concrete_m3': 2.5,
            'masonry_m3': 1.5,
            'plastering_m2': 8.0,
            'flooring_m2': 10.0,
            'steel_kg': 50.0
        }
        
        prod_rate = productivity.get(work_type, 1.0)
        worker_days = quantity / prod_rate
        
        return round(worker_days * labor_rate, 2)

# ============================================================================
# REPORT GENERATOR
# ============================================================================

class ReportGenerator:
    """Generate professional reports in various formats"""
    
    @staticmethod
    def generate_pdf_report(
        project: Project,
        estimation_results: Dict[str, Any]
    ) -> bytes:
        """Generate PDF report using ReportLab"""
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            rightMargin=72, leftMargin=72,
            topMargin=72, bottomMargin=72
        )
        
        styles = getSampleStyleSheet()
        story = []
        
        # Title
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            alignment=TA_CENTER,
            spaceAfter=30
        )
        story.append(Paragraph("QUANTITY ESTIMATION REPORT", title_style))
        story.append(Spacer(1, 20))
        
        # Project Info
        info_style = styles['Normal']
        story.append(Paragraph(f"<b>Project:</b> {project.name}", info_style))
        story.append(Paragraph(f"<b>Client:</b> {project.client_name}", info_style))
        story.append(Paragraph(f"<b>Location:</b> {project.project_location}", info_style))
        story.append(Paragraph(f"<b>Date:</b> {datetime.now().strftime('%Y-%m-%d')}", info_style))
        story.append(Spacer(1, 20))
        
        # Summary Table
        story.append(Paragraph("<b>Material Summary</b>", styles['Heading2']))
        
        summary_data = [['Item', 'Quantity', 'Unit', 'Rate', 'Amount']]
        
        if 'concrete' in estimation_results:
            concrete = estimation_results['concrete']
            summary_data.append([
                'Cement', f"{concrete.get('cement_bags', 0):.2f}", 'Bags',
                f"{project.material_rates.cement_per_bag:.2f}",
                f"{concrete.get('cement_bags', 0) * project.material_rates.cement_per_bag:.2f}"
            ])
            summary_data.append([
                'Sand', f"{concrete.get('sand_m3', 0):.3f}", 'm³',
                f"{project.material_rates.sand_per_m3:.2f}",
                f"{concrete.get('sand_m3', 0) * project.material_rates.sand_per_m3:.2f}"
            ])
            summary_data.append([
                'Aggregate', f"{concrete.get('aggregate_m3', 0):.3f}", 'm³',
                f"{project.material_rates.aggregate_per_m3:.2f}",
                f"{concrete.get('aggregate_m3', 0) * project.material_rates.aggregate_per_m3:.2f}"
            ])
        
        if 'masonry' in estimation_results:
            masonry = estimation_results['masonry']
            summary_data.append([
                'Bricks', f"{masonry.get('bricks_count', 0):,}", 'Nos',
                f"{project.material_rates.brick_per_unit:.2f}",
                f"{masonry.get('bricks_count', 0) * project.material_rates.brick_per_unit:.2f}"
            ])
        
        if 'steel' in estimation_results:
            steel = estimation_results['steel']
            summary_data.append([
                'Steel', f"{steel.get('steel_kg', 0):.2f}", 'kg',
                f"{project.material_rates.steel_per_kg:.2f}",
                f"{steel.get('steel_kg', 0) * project.material_rates.steel_per_kg:.2f}"
            ])
        
        table = Table(summary_data, colWidths=[2*inch, 1.2*inch, 0.8*inch, 1*inch, 1.2*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(table)
        story.append(Spacer(1, 20))
        
        # Notes
        if project.notes:
            story.append(Paragraph("<b>Notes:</b>", styles['Heading2']))
            story.append(Paragraph(project.notes, info_style))
        
        # Build PDF
        doc.build(story)
        buffer.seek(0)
        return buffer.getvalue()
    
    @staticmethod
    def generate_excel_boq(
        project: Project,
        estimation_results: Dict[str, Any]
    ) -> bytes:
        """Generate Excel BOQ"""
        
        buffer = io.BytesIO()
        
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            # Summary sheet
            summary_data = []
            
            if 'concrete' in estimation_results:
                concrete = estimation_results['concrete']
                summary_data.extend([
                    {'Item': 'Cement', 'Quantity': concrete.get('cement_bags', 0), 
                     'Unit': 'Bags', 'Rate': project.material_rates.cement_per_bag},
                    {'Item': 'Sand', 'Quantity': concrete.get('sand_m3', 0), 
                     'Unit': 'm³', 'Rate': project.material_rates.sand_per_m3},
                    {'Item': 'Aggregate', 'Quantity': concrete.get('aggregate_m3', 0), 
                     'Unit': 'm³', 'Rate': project.material_rates.aggregate_per_m3},
                ])
            
            if 'masonry' in estimation_results:
                masonry = estimation_results['masonry']
                summary_data.append({
                    'Item': 'Bricks', 'Quantity': masonry.get('bricks_count', 0),
                    'Unit': 'Nos', 'Rate': project.material_rates.brick_per_unit
                })
            
            if 'steel' in estimation_results:
                steel = estimation_results['steel']
                summary_data.append({
                    'Item': 'Steel', 'Quantity': steel.get('steel_kg', 0),
                    'Unit': 'kg', 'Rate': project.material_rates.steel_per_kg
                })
            
            df_summary = pd.DataFrame(summary_data)
            if not df_summary.empty:
                df_summary['Amount'] = df_summary['Quantity'] * df_summary['Rate']
                df_summary.to_excel(writer, sheet_name='BOQ Summary', index=False)
            
            # Room details sheet
            room_data = []
            for floor in project.floors:
                for room in floor.rooms:
                    room_data.append({
                        'Floor': floor.name,
                        'Room': room.label,
                        'Length (m)': room.length_m,
                        'Width (m)': room.width_m,
                        'Area (m²)': room.area_m2,
                        'Area (ft²)': room.area_ft2
                    })
            
            if room_data:
                pd.DataFrame(room_data).to_excel(
                    writer, sheet_name='Room Details', index=False
                )
        
        buffer.seek(0)
        return buffer.getvalue()

# ============================================================================
# SESSION STATE MANAGEMENT
# ============================================================================

def init_session_state():
    """Initialize session state variables"""
    
    if 'project' not in st.session_state:
        st.session_state.project = Project(
            name="New Project",
            created_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    
    if 'rooms' not in st.session_state:
        st.session_state.rooms = []
    
    if 'estimation_results' not in st.session_state:
        st.session_state.estimation_results = {}
    
    if 'ocr_results' not in st.session_state:
        st.session_state.ocr_results = {'text': '', 'dimensions': []}

def add_room(room: Room):
    """Add a room to session state"""
    st.session_state.rooms.append(room)

def remove_room(room_id: str):
    """Remove a room from session state"""
    st.session_state.rooms = [r for r in st.session_state.rooms if r.id != room_id]

def update_project_rooms():
    """Sync rooms to project"""
    if st.session_state.project.floors:
        st.session_state.project.floors[0].rooms = st.session_state.rooms
    else:
        floor = Floor(floor_number=0, name="Ground Floor", rooms=st.session_state.rooms)
        st.session_state.project.floors.append(floor)

# ============================================================================
# UI COMPONENTS
# ============================================================================

def render_header():
    """Render application header"""
    st.set_page_config(
        page_title="Professional Quantity Estimator",
        page_icon="🏗️",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    st.markdown("""
        <style>
        .main-header {
            font-size: 2.5rem;
            font-weight: bold;
            background: linear-gradient(90deg, #1e3a8a, #3b82f6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
        }
        .sub-header {
            font-size: 1.1rem;
            color: #6b7280;
            margin-bottom: 2rem;
        }
        .metric-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 1rem;
            border-radius: 0.5rem;
            color: white;
        }
        .stTabs [data-baseweb="tab-list"] {
            gap: 2rem;
        }
        .stTabs [data-baseweb="tab"] {
            font-weight: 600;
        }
        </style>
    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown('<p class="main-header">🏗️ Professional Quantity Estimator</p>', 
                    unsafe_allow_html=True)
        st.markdown('<p class="sub-header">From Floor Plan to Bill of Quantities</p>', 
                    unsafe_allow_html=True)
    with col2:
        st.markdown(f"**Date:** {datetime.now().strftime('%Y-%m-%d')}")

def render_sidebar():
    """Render sidebar with project management"""
    
    with st.sidebar:
        st.header("📁 Project Management")
        
        # Project info
        st.session_state.project.name = st.text_input(
            "Project Name", 
            value=st.session_state.project.name
        )
        st.session_state.project.client_name = st.text_input(
            "Client Name",
            value=st.session_state.project.client_name
        )
        st.session_state.project.project_location = st.text_input(
            "Location",
            value=st.session_state.project.project_location
        )
        
        st.divider()
        
        # Save/Load project
        st.subheader("💾 Save / Load")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Save Project", use_container_width=True):
                update_project_rooms()
                project_json = json.dumps(st.session_state.project.to_dict(), indent=2)
                st.download_button(
                    "📥 Download JSON",
                    data=project_json,
                    file_name=f"{st.session_state.project.name.replace(' ', '_')}.json",
                    mime="application/json",
                    use_container_width=True
                )
        
        with col2:
            uploaded_project = st.file_uploader(
                "Load Project",
                type=['json'],
                label_visibility="collapsed"
            )
            if uploaded_project:
                try:
                    project_data = json.load(uploaded_project)
                    st.session_state.project = Project.from_dict(project_data)
                    if st.session_state.project.floors:
                        st.session_state.rooms = st.session_state.project.floors[0].rooms
                    st.success("Project loaded!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to load project: {e}")
        
        st.divider()
        
        # Material rates
        st.subheader("💰 Material Rates")
        
        rates = st.session_state.project.material_rates
        rates.currency = st.selectbox("Currency", ["INR", "USD", "EUR", "GBP"], index=0)
        rates.cement_per_bag = st.number_input(
            f"Cement per bag ({rates.currency})", 
            value=rates.cement_per_bag, 
            step=10.0
        )
        rates.sand_per_m3 = st.number_input(
            f"Sand per m³ ({rates.currency})", 
            value=rates.sand_per_m3, 
            step=50.0
        )
        rates.aggregate_per_m3 = st.number_input(
            f"Aggregate per m³ ({rates.currency})", 
            value=rates.aggregate_per_m3, 
            step=50.0
        )
        rates.brick_per_unit = st.number_input(
            f"Brick per unit ({rates.currency})", 
            value=rates.brick_per_unit, 
            step=0.5
        )
        rates.steel_per_kg = st.number_input(
            f"Steel per kg ({rates.currency})", 
            value=rates.steel_per_kg, 
            step=1.0
        )

def render_image_upload_tab():
    """Render image upload and OCR tab"""

    st.subheader("📷 Upload Floor Plan Image")

    col1, col2 = st.columns([1, 1])

    with col1:
        uploaded_file = st.file_uploader(
            "Upload floor plan (JPG, PNG)",
            type=['jpg', 'jpeg', 'png'],
            help="Upload an architectural floor plan with dimension annotations"
        )

        if uploaded_file:
            image = Image.open(uploaded_file).convert("RGB")
            st.image(image, caption="Uploaded Floor Plan", use_container_width=True)

            # OCR Settings
            with st.expander("🔧 OCR Settings"):
                denoise = st.slider("Denoise Strength", 5, 15, 9)
                threshold_block = st.slider("Threshold Block Size", 11, 51, 35, step=2)

            if st.button("🔍 Extract Dimensions", type="primary", use_container_width=True):
                with st.spinner("Processing image..."):
                    # Extract text
                    raw_text, word_data = ImageProcessor.extract_text_with_confidence(image)

                    # ✅ Clean OCR text
                    cleaned_text = clean_ocr_text(raw_text)

                    # Detect scale
                    scale_info = ImageProcessor.detect_scale(cleaned_text)

                    # Extract dimensions
                    dimensions = DimensionExtractor.extract_dimensions(cleaned_text)

                    # Store results
                    st.session_state.ocr_results = {
                        'text': cleaned_text,
                        'dimensions': dimensions,
                        'scale': scale_info,
                        'word_data': word_data
                    }

                    st.success(f"Found {len(dimensions)} dimension(s)")

    with col2:
        st.subheader("📊 OCR Results")

        ocr_results = st.session_state.get('ocr_results', {})

        if ocr_results.get('text'):
            # Show scale if detected
            if ocr_results.get('scale'):
                scale_val, scale_raw = ocr_results['scale']
                st.info(f"📐 Detected Scale: {scale_raw}")

            # Show extracted text
            with st.expander("📝 Cleaned OCR Text"):
                st.text_area(
                    "Extracted Text",
                    value=ocr_results['text'],
                    height=150
                )

            # Show dimensions
            if ocr_results.get('dimensions'):
                st.markdown("**Detected Dimensions:**")

                for i, dim in enumerate(ocr_results['dimensions']):
                    with st.container():
                        cols = st.columns([2, 1, 1, 1])
                        with cols[0]:
                            st.write(f"📏 `{dim['raw']}`")
                        with cols[1]:
                            st.write(f"{dim['length_m']}m × {dim['width_m']}m")
                        with cols[2]:
                            st.write(f"{dim['area_m2']} m²")
                        with cols[3]:
                            if st.button("➕ Add", key=f"add_dim_{i}"):
                                new_room = Room(
                                    id=f"room_{len(st.session_state.rooms)+1}_{i}",
                                    label=f"Room {len(st.session_state.rooms)+1}",
                                    length_m=dim['length_m'],
                                    width_m=dim['width_m']
                                )
                                add_room(new_room)
                                st.rerun()
            else:
                st.warning("No dimensions detected. Try adjusting OCR settings or add rooms manually.")
        else:
            st.info("Upload an image and click 'Extract Dimensions' to begin")

def render_room_management_tab():
    """Render room management tab"""
    
    st.subheader("🏠 Room Management")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.markdown("**Add New Room**")
        
        with st.form("add_room_form"):
            room_label = st.text_input("Room Label", value=f"Room {len(st.session_state.rooms)+1}")
            
            c1, c2 = st.columns(2)
            with c1:
                room_length = st.number_input("Length (m)", min_value=0.1, value=4.0, step=0.1)
            with c2:
                room_width = st.number_input("Width (m)", min_value=0.1, value=3.5, step=0.1)
            
            room_height = st.number_input("Height (m)", min_value=2.0, value=3.0, step=0.1)
            
            room_type = st.selectbox(
                "Room Type",
                ["Living Room", "Bedroom", "Kitchen", "Bathroom", "Dining", "Study", "Store", "Other"]
            )
            
            submitted = st.form_submit_button("➕ Add Room", type="primary", use_container_width=True)
            
            if submitted:
                new_room = Room(
                    id=f"room_{datetime.now().timestamp()}",
                    label=room_label,
                    length_m=room_length,
                    width_m=room_width,
                    height_m=room_height,
                    room_type=room_type
                )
                add_room(new_room)
                st.success(f"Added {room_label}")
                st.rerun()
    
    with col2:
        st.markdown("**Quick Add (CSV)**")
        
        csv_help = """Format: label,length,width,height
Example:
Living Room,5.5,4.2,3.0
Bedroom 1,4.0,3.5,3.0
Kitchen,3.5,3.0,3.0"""
        
        csv_input = st.text_area("Paste room data", help=csv_help, height=150)
        
        if st.button("📥 Import Rooms", use_container_width=True):
            try:
                for line in csv_input.strip().split('\n'):
                    if line.strip():
                        parts = line.split(',')
                        if len(parts) >= 3:
                            new_room = Room(
                                id=f"room_{datetime.now().timestamp()}_{len(st.session_state.rooms)}",
                                label=parts[0].strip(),
                                length_m=float(parts[1]),
                                width_m=float(parts[2]),
                                height_m=float(parts[3]) if len(parts) > 3 else 3.0
                            )
                            add_room(new_room)
                st.success("Rooms imported!")
                st.rerun()
            except Exception as e:
                st.error(f"Import error: {e}")
    
    st.divider()
    
    # Room list
    st.markdown("### 📋 Room List")
    
    if st.session_state.rooms:
        # Summary metrics
        total_area = sum(r.area_m2 for r in st.session_state.rooms)
        total_area_ft = total_area * 10.7639
        
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Rooms", len(st.session_state.rooms))
        m2.metric("Total Area", f"{total_area:.2f} m²")
        m3.metric("Total Area", f"{total_area_ft:.2f} ft²")
        m4.metric("Avg. Room Size", f"{total_area/len(st.session_state.rooms):.2f} m²")
        
        st.divider()
        
        # Room table with edit/delete
        for i, room in enumerate(st.session_state.rooms):
            with st.container():
                cols = st.columns([0.5, 2, 1, 1, 1, 1, 0.5, 0.5])
                
                cols[0].write(f"**{i+1}**")
                cols[1].write(f"**{room.label}**")
                cols[2].write(f"📏 {room.length_m}m × {room.width_m}m")
                cols[3].write(f"📐 {room.area_m2:.2f} m²")
                cols[4].write(f"📐 {room.area_ft2:.2f} ft²")
                cols[5].write(f"🏷️ {room.room_type}")
                
                if cols[6].button("✏️", key=f"edit_{room.id}"):
                    st.session_state[f"editing_{room.id}"] = True
                
                if cols[7].button("🗑️", key=f"del_{room.id}"):
                    remove_room(room.id)
                    st.rerun()
                
                # Edit form
                if st.session_state.get(f"editing_{room.id}"):
                    with st.expander("Edit Room", expanded=True):
                        ec1, ec2, ec3, ec4 = st.columns(4)
                        new_label = ec1.text_input("Label", value=room.label, key=f"lbl_{room.id}")
                        new_length = ec2.number_input("Length", value=room.length_m, key=f"len_{room.id}")
                        new_width = ec3.number_input("Width", value=room.width_m, key=f"wid_{room.id}")
                        new_height = ec4.number_input("Height", value=room.height_m, key=f"hgt_{room.id}")
                        
                        if st.button("Save", key=f"save_{room.id}"):
                            room.label = new_label
                            room.length_m = new_length
                            room.width_m = new_width
                            room.height_m = new_height
                            st.session_state[f"editing_{room.id}"] = False
                            st.rerun()
                
                st.divider()
    else:
        st.info("No rooms added yet. Use the form above or import from OCR results.")

def render_estimation_tab():
    """Render estimation parameters and calculations tab"""
    
    st.subheader("📊 Material Estimation")
    
    if not st.session_state.rooms:
        st.warning("Please add rooms first in the Room Management tab")
        return
    
    # Calculate totals
    total_area = sum(r.area_m2 for r in st.session_state.rooms)
    total_perimeter = sum(r.perimeter_m for r in st.session_state.rooms)
    
    # Parameters
    st.markdown("### ⚙️ Estimation Parameters")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("**Concrete Works**")
        slab_thickness = st.number_input("Slab Thickness (m)", value=0.12, step=0.01)
        concrete_grade = st.selectbox("Concrete Grade", list(CONCRETE_GRADES.keys()), index=1)
        extra_concrete = st.number_input("Extra Concrete (beams/columns) m³", value=0.0, step=0.5)
    
    with col2:
        st.markdown("**Masonry Works**")
        wall_height = st.number_input("Wall Height (m)", value=3.0, step=0.1)
        wall_thickness = st.number_input("Wall Thickness (m)", value=0.23, step=0.01)
        brick_type = st.selectbox("Brick Type", list(BRICK_TYPES.keys()))
        opening_deduction = st.slider("Openings Deduction %", 0, 40, 15)
    
    with col3:
        st.markdown("**Steel & Finishes**")
        steel_pct = st.number_input("Steel % (of concrete)", value=1.0, step=0.1)
        plaster_thickness = st.number_input("Plaster Thickness (mm)", value=12, step=1)
        tile_size = st.selectbox("Tile Size (mm)", ["600x600", "400x400", "300x300"])
    
    st.divider()
    
    # Wall length options
    st.markdown("**Wall Length Input**")
    wall_option = st.radio(
        "How to calculate wall length?",
        ["Auto-estimate from room perimeters", "Provide manual wall length"],
        horizontal=True
    )
    
    if wall_option == "Provide manual wall length":
        wall_length = st.number_input("Total Wall Length (m)", value=total_perimeter, step=1.0)
    else:
        wall_length = total_perimeter
        st.info(f"Auto-calculated wall length: {wall_length:.2f} m")
    
    st.divider()
    
    # Run estimation
    if st.button("🧮 Calculate Estimation", type="primary", use_container_width=True):
        
        results = {}
        
        # Concrete calculation
        slab_volume = total_area * slab_thickness
        total_concrete = slab_volume + extra_concrete
        
        grade = CONCRETE_GRADES[concrete_grade]
        concrete_mats = MaterialCalculator.calculate_concrete_materials(total_concrete, grade)
        results['concrete'] = concrete_mats
        
        # Steel calculation
        steel_results = MaterialCalculator.calculate_steel_reinforcement(
            total_concrete, 'slab', steel_pct
        )
        results['steel'] = steel_results
        
        # Masonry calculation
        brick = BRICK_TYPES[brick_type]
        masonry_results = MaterialCalculator.calculate_masonry(
            wall_length, wall_height, wall_thickness,
            brick, opening_deduction_pct=opening_deduction/100
        )
        results['masonry'] = masonry_results
        
        # Plastering (both sides of wall + ceiling)
        plaster_area = (masonry_results['net_wall_area_m2'] * 2) + total_area  # walls + ceiling
        plaster_results = MaterialCalculator.calculate_plastering(plaster_area, plaster_thickness)
        results['plastering'] = plaster_results
        
        # Flooring
        tile_dims = tuple(map(int, tile_size.split('x')))
        flooring_results = MaterialCalculator.calculate_flooring(total_area, tile_dims)
        results['flooring'] = flooring_results
        
        # Cost calculation
        all_materials = {
            'cement_bags': (concrete_mats['cement_bags'] + masonry_results['mortar_cement_bags'] + 
                          plaster_results['cement_bags']),
            'sand_m3': concrete_mats['sand_m3'] + masonry_results['mortar_sand_m3'] + plaster_results['sand_m3'],
            'aggregate_m3': concrete_mats['aggregate_m3'],
            'bricks_count': masonry_results['bricks_count'],
            'steel_kg': steel_results['steel_kg'],
            'water_liters': concrete_mats['water_liters']
        }
        
        costs = CostEstimator.calculate_material_costs(
            all_materials, 
            st.session_state.project.material_rates
        )
        results['costs'] = costs
        
        # Store results
        st.session_state.estimation_results = results
        
        st.success("Estimation completed!")

def render_results_tab():
    """Render estimation results and reports"""
    
    st.subheader("📊 Estimation Results")
    
    results = st.session_state.estimation_results
    
    if not results:
        st.warning("No estimation results yet. Run the estimation first.")
        return
    
    rates = st.session_state.project.material_rates
    
    # Summary metrics
    st.markdown("### 📈 Summary")
    
    total_area = sum(r.area_m2 for r in st.session_state.rooms)
    
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Built-up Area", f"{total_area:.2f} m²")
    
    if 'concrete' in results:
        m2.metric("Total Concrete", f"{results['concrete']['wet_volume_m3']:.2f} m³")
    
    if 'masonry' in results:
        m3.metric("Total Bricks", f"{results['masonry']['bricks_count']:,}")
    
    if 'costs' in results:
        m4.metric(
            f"Total Cost ({rates.currency})", 
            f"{results['costs']['total_material_cost']:,.2f}"
        )
    
    st.divider()
    
    # Detailed results in tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🏗️ Concrete", "🧱 Masonry", "⚙️ Steel", "🎨 Finishes", "💰 Costs"
    ])
    
    with tab1:
        if 'concrete' in results:
            st.markdown("#### Concrete Materials")
            concrete = results['concrete']
            
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Wet Volume", f"{concrete['wet_volume_m3']:.3f} m³")
                st.metric("Cement", f"{concrete['cement_bags']:.2f} bags ({concrete['cement_kg']:.2f} kg)")
                st.metric("Sand", f"{concrete['sand_m3']:.3f} m³")
            with col2:
                st.metric("Dry Volume", f"{concrete['dry_volume_m3']:.3f} m³")
                st.metric("Aggregate", f"{concrete['aggregate_m3']:.3f} m³")
                st.metric("Water", f"{concrete['water_liters']:.2f} liters")
    
    with tab2:
        if 'masonry' in results:
            st.markdown("#### Masonry Materials")
            masonry = results['masonry']
            
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Wall Area (Gross)", f"{masonry['gross_wall_area_m2']:.2f} m²")
                st.metric("Wall Area (Net)", f"{masonry['net_wall_area_m2']:.2f} m²")
                st.metric("Wall Volume", f"{masonry['wall_volume_m3']:.3f} m³")
            with col2:
                st.metric("Bricks Required", f"{masonry['bricks_count']:,}")
                st.metric("Mortar Cement", f"{masonry['mortar_cement_bags']:.2f} bags")
                st.metric("Mortar Sand", f"{masonry['mortar_sand_m3']:.3f} m³")
    
    with tab3:
        if 'steel' in results:
            st.markdown("#### Steel Reinforcement")
            steel = results['steel']
            
            st.metric("Steel Percentage", f"{steel['steel_percentage']}%")
            st.metric("Steel Weight", f"{steel['steel_kg']:.2f} kg ({steel['steel_tonnes']:.3f} tonnes)")
    
    with tab4:
        col1, col2 = st.columns(2)
        
        with col1:
            if 'plastering' in results:
                st.markdown("#### Plastering")
                plaster = results['plastering']
                st.metric("Plaster Area", f"{plaster['plaster_area_m2']:.2f} m²")
                st.metric("Cement", f"{plaster['cement_bags']:.2f} bags")
                st.metric("Sand", f"{plaster['sand_m3']:.3f} m³")
        
        with col2:
            if 'flooring' in results:
                st.markdown("#### Flooring")
                floor = results['flooring']
                st.metric("Floor Area", f"{floor['floor_area_m2']:.2f} m²")
                st.metric(f"Tiles ({floor['tile_size']})", f"{floor['tiles_count']} nos")
                st.metric("Tile Adhesive", f"{floor['adhesive_kg']:.2f} kg")
    
    with tab5:
        if 'costs' in results:
            st.markdown("#### Cost Breakdown")
            costs = results['costs']
            
            # Cost table
            cost_data = [
                {"Item": "Cement", "Cost": costs.get('cement_cost', 0)},
                {"Item": "Sand", "Cost": costs.get('sand_cost', 0)},
                {"Item": "Aggregate", "Cost": costs.get('aggregate_cost', 0)},
                {"Item": "Bricks", "Cost": costs.get('brick_cost', 0)},
                {"Item": "Steel", "Cost": costs.get('steel_cost', 0)},
                {"Item": "Water", "Cost": costs.get('water_cost', 0)},
            ]
            
            df_costs = pd.DataFrame(cost_data)
            df_costs['Cost'] = df_costs['Cost'].apply(lambda x: f"{rates.currency} {x:,.2f}")
            st.dataframe(df_costs, use_container_width=True)
            
            st.metric(
                f"Total Material Cost ({rates.currency})", 
                f"{costs['total_material_cost']:,.2f}"
            )
            
        
            # Pie chart for cost breakdown
            fig = px.pie(
                values=[costs.get('cement_cost', 0), costs.get('sand_cost', 0), 
                        costs.get('aggregate_cost', 0), costs.get('brick_cost', 0),
                        costs.get('steel_cost', 0), costs.get('water_cost', 0)],
                names=['Cement', 'Sand', 'Aggregate', 'Bricks', 'Steel', 'Water'],
                title='Material Cost Distribution',
                hole=0.4
            )
            fig.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig, use_container_width=True)
    
    st.divider()
    
    # Export options
    st.markdown("### 📥 Export Reports")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        # CSV Export
        if st.button("📄 Export CSV", use_container_width=True):
            update_project_rooms()
            
            # Create summary DataFrame
            summary_data = []
            
            if 'concrete' in results:
                c = results['concrete']
                summary_data.extend([
                    {'Category': 'Concrete', 'Item': 'Cement', 'Quantity': c['cement_bags'], 
                     'Unit': 'Bags', 'Rate': rates.cement_per_bag, 
                     'Amount': c['cement_bags'] * rates.cement_per_bag},
                    {'Category': 'Concrete', 'Item': 'Sand', 'Quantity': c['sand_m3'], 
                     'Unit': 'm³', 'Rate': rates.sand_per_m3,
                     'Amount': c['sand_m3'] * rates.sand_per_m3},
                    {'Category': 'Concrete', 'Item': 'Aggregate', 'Quantity': c['aggregate_m3'], 
                     'Unit': 'm³', 'Rate': rates.aggregate_per_m3,
                     'Amount': c['aggregate_m3'] * rates.aggregate_per_m3},
                ])
            
            if 'masonry' in results:
                m = results['masonry']
                summary_data.extend([
                    {'Category': 'Masonry', 'Item': 'Bricks', 'Quantity': m['bricks_count'], 
                     'Unit': 'Nos', 'Rate': rates.brick_per_unit,
                     'Amount': m['bricks_count'] * rates.brick_per_unit},
                    {'Category': 'Masonry', 'Item': 'Mortar Cement', 'Quantity': m['mortar_cement_bags'], 
                     'Unit': 'Bags', 'Rate': rates.cement_per_bag,
                     'Amount': m['mortar_cement_bags'] * rates.cement_per_bag},
                    {'Category': 'Masonry', 'Item': 'Mortar Sand', 'Quantity': m['mortar_sand_m3'], 
                     'Unit': 'm³', 'Rate': rates.sand_per_m3,
                     'Amount': m['mortar_sand_m3'] * rates.sand_per_m3},
                ])
            
            if 'steel' in results:
                s = results['steel']
                summary_data.append({
                    'Category': 'Steel', 'Item': 'Reinforcement', 'Quantity': s['steel_kg'], 
                    'Unit': 'kg', 'Rate': rates.steel_per_kg,
                    'Amount': s['steel_kg'] * rates.steel_per_kg
                })
            
            if 'plastering' in results:
                p = results['plastering']
                summary_data.extend([
                    {'Category': 'Plastering', 'Item': 'Cement', 'Quantity': p['cement_bags'], 
                     'Unit': 'Bags', 'Rate': rates.cement_per_bag,
                     'Amount': p['cement_bags'] * rates.cement_per_bag},
                    {'Category': 'Plastering', 'Item': 'Sand', 'Quantity': p['sand_m3'], 
                     'Unit': 'm³', 'Rate': rates.sand_per_m3,
                     'Amount': p['sand_m3'] * rates.sand_per_m3},
                ])
            
            if 'flooring' in results:
                f = results['flooring']
                summary_data.append({
                    'Category': 'Flooring', 'Item': 'Tiles', 'Quantity': f['tiles_count'], 
                    'Unit': 'Nos', 'Rate': 50.0,  # Default tile rate
                    'Amount': f['tiles_count'] * 50.0
                })
            
            df_export = pd.DataFrame(summary_data)
            csv_buffer = io.StringIO()
            df_export.to_csv(csv_buffer, index=False)
            
            st.download_button(
                "📥 Download CSV",
                data=csv_buffer.getvalue(),
                file_name=f"BOQ_{st.session_state.project.name}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )
    
    with col2:
        # Excel Export
        if st.button("📊 Export Excel", use_container_width=True):
            update_project_rooms()
            excel_data = ReportGenerator.generate_excel_boq(
                st.session_state.project, 
                results
            )
            st.download_button(
                "📥 Download Excel",
                data=excel_data,
                file_name=f"BOQ_{st.session_state.project.name}_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
    
    with col3:
        # PDF Export
        if st.button("📑 Export PDF", use_container_width=True):
            update_project_rooms()
            try:
                pdf_data = ReportGenerator.generate_pdf_report(
                    st.session_state.project, 
                    results
                )
                st.download_button(
                    "📥 Download PDF",
                    data=pdf_data,
                    file_name=f"Report_{st.session_state.project.name}_{datetime.now().strftime('%Y%m%d')}.pdf",
                    mime="application/pdf"
                )
            except Exception as e:
                st.error(f"PDF generation failed: {e}. Please ensure reportlab is installed.")

def render_analytics_tab():
    """Render visual analytics and charts"""
    
    st.subheader("📈 Visual Analytics")
    
    if not st.session_state.rooms:
        st.warning("Add rooms to see analytics")
        return
    
    results = st.session_state.estimation_results
    
    # Room area distribution
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("#### Room Area Distribution")
        room_data = pd.DataFrame([
            {'Room': r.label, 'Area (m²)': r.area_m2, 'Type': r.room_type}
            for r in st.session_state.rooms
        ])
        
        fig_rooms = px.bar(
            room_data, 
            x='Room', 
            y='Area (m²)', 
            color='Type',
            title='Room Areas'
        )
        fig_rooms.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig_rooms, use_container_width=True)
    
    with col2:
        st.markdown("#### Room Type Breakdown")
        type_summary = room_data.groupby('Type')['Area (m²)'].sum().reset_index()
        
        fig_types = px.pie(
            type_summary, 
            values='Area (m²)', 
            names='Type',
            title='Area by Room Type',
            hole=0.3
        )
        st.plotly_chart(fig_types, use_container_width=True)
    
    if results:
        st.divider()
        st.markdown("#### Material Quantities Overview")
        
        # Create material comparison chart
        materials = []
        
        if 'concrete' in results:
            c = results['concrete']
            materials.extend([
                {'Material': 'Cement (Concrete)', 'Quantity': c['cement_bags'], 'Unit': 'Bags'},
                {'Material': 'Sand (Concrete)', 'Quantity': c['sand_m3'] * 100, 'Unit': 'm³ x100'},
                {'Material': 'Aggregate', 'Quantity': c['aggregate_m3'] * 100, 'Unit': 'm³ x100'},
            ])
        
        if 'masonry' in results:
            m = results['masonry']
            materials.extend([
                {'Material': 'Cement (Mortar)', 'Quantity': m['mortar_cement_bags'], 'Unit': 'Bags'},
                {'Material': 'Bricks', 'Quantity': m['bricks_count'] / 100, 'Unit': 'x100 Nos'},
            ])
        
        if 'steel' in results:
            materials.append({
                'Material': 'Steel', 
                'Quantity': results['steel']['steel_kg'] / 10, 
                'Unit': 'x10 kg'
            })
        
        if materials:
            df_materials = pd.DataFrame(materials)
            
            fig_materials = px.bar(
                df_materials,
                x='Material',
                y='Quantity',
                color='Material',
                title='Material Quantities (Normalized Scale)'
            )
            fig_materials.update_layout(xaxis_tickangle=-45, showlegend=False)
            st.plotly_chart(fig_materials, use_container_width=True)
        
        # Cost breakdown Sankey diagram
        if 'costs' in results:
            st.markdown("#### Cost Flow Analysis")
            
            costs = results['costs']
            
            labels = ['Total Budget', 'Concrete Works', 'Masonry Works', 'Steel Works',
                     'Cement', 'Sand', 'Aggregate', 'Bricks', 'Steel']
            
            # Calculate intermediate totals
            concrete_total = costs.get('cement_cost', 0) * 0.6 + costs.get('sand_cost', 0) * 0.6 + costs.get('aggregate_cost', 0)
            masonry_total = costs.get('brick_cost', 0) + costs.get('cement_cost', 0) * 0.3 + costs.get('sand_cost', 0) * 0.3
            steel_total = costs.get('steel_cost', 0)
            
            fig_sankey = go.Figure(data=[go.Sankey(
                node=dict(
                    pad=15,
                    thickness=20,
                    line=dict(color="black", width=0.5),
                    label=labels,
                    color=["#1e3a8a", "#3b82f6", "#f59e0b", "#10b981", 
                           "#6366f1", "#ec4899", "#8b5cf6", "#f97316", "#14b8a6"]
                ),
                link=dict(
                    source=[0, 0, 0, 1, 1, 1, 2, 2, 3],
                    target=[1, 2, 3, 4, 5, 6, 7, 5, 8],
                    value=[concrete_total, masonry_total, steel_total,
                           costs.get('cement_cost', 0) * 0.6,
                           costs.get('sand_cost', 0) * 0.6,
                           costs.get('aggregate_cost', 0),
                           costs.get('brick_cost', 0),
                           costs.get('sand_cost', 0) * 0.3,
                           costs.get('steel_cost', 0)]
                )
            )])
            
            fig_sankey.update_layout(title_text="Cost Flow Diagram", font_size=12)
            st.plotly_chart(fig_sankey, use_container_width=True)

def render_help_tab():
    """Render help and documentation tab"""
    
    st.subheader("📚 Help & Documentation")
    
    with st.expander("🏗️ How to Use This Application", expanded=True):
        st.markdown("""
        ### Quick Start Guide
        
        1. **Upload Floor Plan** (Optional)
           - Go to the "Image Upload" tab
           - Upload a floor plan image with dimension annotations
           - Click "Extract Dimensions" to detect room sizes via OCR
           - Add detected dimensions to your room list
        
        2. **Add Rooms Manually**
           - Go to the "Room Management" tab
           - Enter room dimensions manually or import via CSV
           - Edit or delete rooms as needed
        
        3. **Configure Parameters**
           - Go to the "Estimation" tab
           - Set slab thickness, wall dimensions, concrete grade, etc.
           - Adjust material rates in the sidebar
        
        4. **Run Estimation**
           - Click "Calculate Estimation" to compute material quantities
           - Review results in the "Results" tab
        
        5. **Export Reports**
           - Download CSV, Excel, or PDF reports
           - Save your project as JSON for future use
        """)
    
    with st.expander("📐 Estimation Formulas"):
        st.markdown("""
        ### Concrete Materials (1:2:4 Mix)
        
        For 1 m³ of wet concrete:
        - **Dry Volume** = 1.54 × Wet Volume (shrinkage factor)
        - **Cement** = Dry Volume × (1/7) × 1.05 (wastage)
        - **Sand** = Dry Volume × (2/7) × 1.10 (wastage)
        - **Aggregate** = Dry Volume × (4/7) × 1.05 (wastage)
        
        ### Masonry Bricks
        
        - **Bricks per m³** ≈ 500 (for standard 230×110×75mm bricks)
        - **Mortar Volume** ≈ 25% of wall volume
        - **Deduct** 15-20% for door/window openings
        
        ### Steel Reinforcement
        
        - **Slab**: 0.7-1.0% of concrete volume
        - **Beam**: 1.0-2.0% of concrete volume  
        - **Column**: 2.0-4.0% of concrete volume
        - **Steel Weight** = Concrete Volume × Steel % × 7850 kg/m³
        """)
    
    with st.expander("🧱 Material Specifications"):
        st.markdown("""
        ### Concrete Grades
        
        | Grade | Mix Ratio (C:S:A) | W/C Ratio | Strength (MPa) |
        |-------|-------------------|-----------|----------------|
        | M15   | 1:2:4             | 0.60      | 15             |
        | M20   | 1:1.5:3           | 0.55      | 20             |
        | M25   | 1:1:2             | 0.50      | 25             |
        | M30   | 1:0.75:1.5        | 0.45      | 30             |
        
        ### Brick Types
        
        | Type     | Size (mm)     | Bricks/m³ |
        |----------|---------------|-----------|
        | Standard | 230×110×75    | 500       |
        | Modular  | 190×90×90     | 588       |
        | Jumbo    | 230×110×150   | 250       |
        
        ### Cement Bag
        
        - **Weight**: 50 kg per bag
        - **Volume**: ~0.0347 m³ (bulk density ~1440 kg/m³)
        """)
    
    with st.expander("⚠️ Important Disclaimers"):
        st.markdown("""
        ### Disclaimer
        
        This tool provides **estimates only** and should be used for:
        - Preliminary cost budgeting
        - Quick material takeoffs
        - Educational purposes
        
        **NOT suitable for:**
        - Final tender/contract quantities
        - Structural design calculations
        - Replacing professional quantity surveyor services
        
        ### Recommendations
        
        1. Always verify OCR-extracted dimensions manually
        2. Adjust wastage percentages based on site conditions
        3. Consult a structural engineer for reinforcement details
        4. Get actual quotes from suppliers for accurate costing
        5. Include contingency (10-15%) in final budgets
        """)

# ============================================================================
# MAIN APPLICATION
# ============================================================================

def main():
    """Main application entry point"""
    
    # Initialize session state
    init_session_state()
    
    # Render header
    render_header()
    
    # Render sidebar
    render_sidebar()
    
    # Main content tabs
    tabs = st.tabs([
        "📷 Image Upload",
        "🏠 Room Management", 
        "📊 Estimation",
        "📋 Results",
        "📈 Analytics",
        "❓ Help"
    ])
    
    with tabs[0]:
        render_image_upload_tab()
    
    with tabs[1]:
        render_room_management_tab()
    
    with tabs[2]:
        render_estimation_tab()
    
    with tabs[3]:
        render_results_tab()
    
    with tabs[4]:
        render_analytics_tab()
    
    with tabs[5]:
        render_help_tab()
    
    # Footer
    st.divider()
    st.markdown(
        """
        <div style='text-align: center; color: #6b7280; font-size: 0.85rem;'>
        Professional Quantity Estimator v2.0 | 
        Built with Streamlit | 
        © 2025 - For estimation purposes only
        </div>
        """,
        unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()

