"""init: схема БД (таблицы без данных)

Revision ID: 0a483c192000
Revises:
Create Date: 2026-03-09 13:03:03.292455

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0a483c192000'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'complications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=False),
        sa.Column('source_type', sa.Enum('DISEASE', 'TRAUMA', name='complicationsource'), nullable=False),
        sa.Column('disease_comp_type', sa.Enum('LIGHT', 'SEVERE', name='diseasecomptype'), nullable=True),
        sa.Column('trauma_code', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table(
        'diseases',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=False, server_default=''),
        sa.Column('type', sa.Enum('WOUND', 'TRAUMA', 'SYMPTOM', name='diseasetype'), nullable=False),
        sa.Column('trauma_code', sa.Integer(), nullable=True),
        sa.Column('operation', sa.Boolean(), nullable=False),
        sa.Column('kind', sa.Enum('NON_COMBAT', 'KNIFE', 'BULLET', name='diseasekind'), nullable=True),
        sa.Column('energy', sa.Boolean(), nullable=False),
        sa.Column('layers', postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column('health_only', sa.Boolean(), nullable=False),
        sa.Column('strength', sa.Integer(), nullable=False),
        sa.Column('pain', sa.Integer(), nullable=False),
        sa.Column('light_complication', sa.Boolean(), nullable=False),
        sa.Column('severe_complication', sa.Boolean(), nullable=False),
        sa.Column('hidden_from_getting', sa.Boolean(), nullable=False, server_default='false'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table(
        'locations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('infection_chance', sa.Integer(), nullable=False),
        sa.Column('capacity', sa.Integer(), nullable=False),
        sa.Column('quality', sa.Boolean(), nullable=False),
        sa.Column('pain_disease_mod', sa.Integer(), nullable=False),
        sa.Column('pain_wound_mod', sa.Integer(), nullable=False),
        sa.Column('cure_mod', sa.Integer(), nullable=False),
        sa.Column('light_comp_mod', sa.Integer(), nullable=False),
        sa.Column('severe_comp_mod', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code')
    )
    op.create_table(
        'skills',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=False),
        sa.Column('is_health', sa.Boolean(), nullable=False),
        sa.Column('pain', sa.Integer(), nullable=False),
        sa.Column('recipes', postgresql.ARRAY(sa.Enum('REPAIR_CLOAK', 'REPAIR_BOOTS', 'REPAIR_GLOVES', 'REPAIR_MASKS', 'REPAIR_BANDIT_MASKS', 'REPAIR_KNIVES', 'REPAIR_GUNS', 'CRAFT_LOCKPICKS', 'CRAFT_TWYRINE', 'UPGRADE_LOCKS', 'CRAFT_IMMUNICS', 'CRAFT_ANTIBIOTICS', 'CRAFT_PAINKILLERS', 'CRAFT_CLOAKS', 'CRAFT_BOOTS', 'CRAFT_GLOVES', 'CRAFT_MASKS', 'CRAFT_BANDIT_MASKS', 'CRAFT_KNIVES', 'CRAFT_LOCKS', 'REPAIR_ARMY_CLOAKS', 'CRAFT_SPECIAL_MEDS', name='recipe')), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('character_name', sa.String(), nullable=False),
        sa.Column('tg_username', sa.String(), nullable=False),
        sa.Column('vk_username', sa.String(), nullable=True),
        sa.Column('telegram_id', sa.BigInteger(), nullable=True),
        sa.Column('vk_id', sa.BigInteger(), nullable=True),
        sa.Column('tg_connected', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('vk_connected', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('is_admin', sa.Boolean(), nullable=False),
        sa.Column('is_alive', sa.Boolean(), nullable=False),
        sa.Column('is_child', sa.Boolean(), nullable=False),
        sa.Column('weak_zones', postgresql.ARRAY(sa.Enum('HEAD', 'CHEST', 'LEFT_ARM', 'RIGHT_ARM', 'LEFT_LEG', 'RIGHT_LEG', name='weakzone')), nullable=False),
        sa.Column('twyrine_addiction', sa.Boolean(), nullable=False),
        sa.Column('last_infection_time', sa.DateTime(), nullable=True),
        sa.Column('last_cure_time', sa.DateTime(), nullable=True),
        sa.Column('infection_status', sa.Enum('VACCINATED', 'HEALTHY', 'INFECTED', name='infectionstatus'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tg_username'),
        sa.UniqueConstraint('vk_username'),
        sa.UniqueConstraint('telegram_id'),
        sa.UniqueConstraint('vk_id')
    )
    op.create_table(
        'medicines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.Integer(), nullable=True),
        sa.Column('med_type', sa.Enum('ANTIBIOTIC', 'IMMUNIC', 'PAINKILLER', 'SPECIAL', 'NON_WORKING', 'POWDER', 'PANACEA', 'VACCINE', name='medtype'), nullable=False),
        sa.Column('cure_layer_1', sa.Integer(), nullable=False),
        sa.Column('cure_layer_2', sa.Integer(), nullable=False),
        sa.Column('cure_layer_3', sa.Integer(), nullable=False),
        sa.Column('pain', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code')
    )
    op.create_table(
        'slots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('layer', sa.Integer(), nullable=False),
        sa.Column('skill_id', sa.Integer(), nullable=True),
        sa.Column('disease_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['disease_id'], ['diseases.id']),
        sa.ForeignKeyConstraint(['skill_id'], ['skills.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table(
        'user_actions',
        sa.Column('id', sa.Integer(), sa.Identity(start=1, increment=1), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(), nullable=True),
        sa.Column('chat_id', sa.Integer(), nullable=False),
        sa.Column('action_type', sa.String(), nullable=False),
        sa.Column('details', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_actions_user_id', 'user_actions', ['user_id'])
    op.create_index('ix_user_actions_created_at', 'user_actions', ['created_at'])
    op.create_table(
        'game_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('night_active', sa.Boolean(), nullable=False),
        sa.Column('pause_active', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('pain_disease_mod', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('pain_wound_mod', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('cure_mod', sa.Integer(), nullable=False, server_default='-1'),
        sa.Column('light_comp_mod', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('severe_comp_mod', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('pain_death_threshold', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('pain_consequence_divisor', sa.Integer(), nullable=False, server_default='3'),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('game_settings')
    op.drop_index('ix_user_actions_created_at', table_name='user_actions')
    op.drop_index('ix_user_actions_user_id', table_name='user_actions')
    op.drop_table('user_actions')
    op.drop_table('slots')
    op.drop_table('medicines')
    op.drop_table('users')
    op.drop_table('skills')
    op.drop_table('locations')
    op.drop_table('diseases')
    op.drop_table('complications')
