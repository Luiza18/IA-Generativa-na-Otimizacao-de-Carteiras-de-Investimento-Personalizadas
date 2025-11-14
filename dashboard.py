import streamlit as st
import pandas as pd
import json
import plotly.express as px
from datetime import datetime, timedelta
from classes.PostgreSQL import PostgresSQL
from pypika import Table, Query

# === Leitura do Secret do Streamlit ===
DB_CONNECTION_STRING = st.secrets["DB_CONNECTION_STRING"]

# Debug temporário — delete após testar
st.write("DEBUG: DB_CONNECTION_STRING =", DB_CONNECTION_STRING)

st.set_page_config(layout="wide", page_title="Dashboard de Carteiras")


# --- Funções de Busca de Dados ---
@st.cache_resource(ttl=600)
def get_db_connection():
    try:
        return PostgresSQL(DB_CONNECTION_STRING)
    except Exception as e:
        st.error(f"Erro ao conectar ao banco de dados: {e}")
        return None


@st.cache_data(ttl=600)
def load_json_data(filepath="carteiras_otimizadas.json"):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        st.error(f"Erro: Arquivo '{filepath}' não encontrado.")
        st.info("Por favor, execute o script principal de cálculo de carteira primeiro.")
        return None
    except Exception as e:
        st.error(f"Erro ao ler o arquivo JSON: {e}")
        return None


@st.cache_data(ttl=3600)
def get_data_for_dashboard(_db_conn, tickers_carteira: list):
    if not tickers_carteira:
        return pd.DataFrame(columns=['TICKER', 'SEGMENTO', 'CATEGORIA']), pd.DataFrame()
    
    ativos_table = Table("ATIVOS")
    categorias_table = Table("CATEGORIAS")
    
    q_info = (
        Query.from_(ativos_table)
        .left_join(categorias_table)
        .on(ativos_table.CATEGORIA == categorias_table.id)
        .select(
            ativos_table.TICKER,
            ativos_table.SEGMENTO,
            categorias_table.CATEGORIA
        )
        .where(ativos_table.TICKER.isin(tickers_carteira))
    )
    
    df_info_ativos = _db_conn.query(q_info.get_sql())
    
    if 'CATEGORIA' in df_info_ativos.columns:
        df_info_ativos['CATEGORIA'] = df_info_ativos['CATEGORIA'].fillna('Não Categorizado')
    else:
        df_info_ativos['CATEGORIA'] = 'Não Categorizado'
        
    if 'SEGMENTO' in df_info_ativos.columns:
        df_info_ativos['SEGMENTO'] = df_info_ativos['SEGMENTO'].fillna('Não Classificado')
    else:
        df_info_ativos['SEGMENTO'] = 'Não Classificado'

    data_limite = datetime.now() - timedelta(days=95)
    precos_table = Table("PRECOS")
    
    q_precos = (
        Query.from_(precos_table)
        .select(precos_table.DATA, precos_table.TICKER, precos_table.PRECO)
        .where(precos_table.TICKER.isin(tickers_carteira))
        .where(precos_table.DATA >= data_limite)
        .orderby(precos_table.DATA)
    )
    
    try:
        df_precos = _db_conn.query(q_precos.get_sql())
        if df_precos.empty:
            st.warning("Nenhum dado de preço encontrado nos últimos 3 meses.")
            return df_info_ativos, pd.DataFrame()
                
        df_precos_pivot = df_precos.pivot(
            index='DATA', columns='TICKER', values='PRECO'
        )
        df_precos_pivot.index = pd.to_datetime(df_precos_pivot.index)
        df_precos_pivot = df_precos_pivot.ffill().dropna(axis=1, how='all')
        
        return df_info_ativos, df_precos_pivot

    except Exception as e:
        st.error(f"Erro ao buscar preços históricos: {e}")
        return df_info_ativos, pd.DataFrame()


def create_backtest_df(precos_pivot: pd.DataFrame, pesos: dict):
    portfolio_cols_existentes = [col for col in pesos.keys() if col in precos_pivot.columns]
    precos_carteira = precos_pivot[portfolio_cols_existentes]
    
    if precos_carteira.empty:
        st.warning("Não foi possível calcular o rendimento (ativos sem dados de preço).")
        return pd.DataFrame()
    
    pesos_series = pd.Series(pesos).reindex(precos_carteira.columns).fillna(0)
    retornos_diarios_ativos = precos_carteira.pct_change()
    retorno_diario_carteira = (retornos_diarios_ativos * pesos_series).sum(axis=1)
    
    df_retornos = pd.DataFrame({'Carteira_Factor': 1 + retorno_diario_carteira}).dropna()
    
    if df_retornos.empty:
        return pd.DataFrame()
    
    df_final = pd.DataFrame(index=df_retornos.index)
    df_final['Carteira (Base 100)'] = df_retornos['Carteira_Factor'].cumprod()
    df_final['Carteira (Base 100)'] = df_final['Carteira (Base 100)'] / df_final['Carteira (Base 100)'].iloc[0] * 100
    
    return df_final


def create_individual_return_df(precos_pivot: pd.DataFrame, pesos: dict):
    portfolio_cols_existentes = [col for col in pesos.keys() if col in precos_pivot.columns]
    precos_carteira = precos_pivot[portfolio_cols_existentes]
    
    if precos_carteira.empty:
        return pd.DataFrame()
    
    df_rendimento = ((precos_carteira / precos_carteira.iloc[0]) - 1) * 100
    return df_rendimento


# --- Construção do Dashboard ---
st.title("📈 Dashboard de Otimização de Carteiras")

db_conn = get_db_connection()
data = load_json_data()

if db_conn is None or data is None:
    st.stop()

perfil_conservador, perfil_moderado, perfil_arrojado = st.tabs(
    ["Conservador", "Moderado", "Arrojado"]
)

for perfil_nome, aba in [
    ("conservador", perfil_conservador),
    ("moderado", perfil_moderado),
    ("arrojado", perfil_arrojado)
]:
    
    if perfil_nome not in data:
        aba.warning(f"Dados da carteira '{perfil_nome}' não encontrados no JSON.")
        continue

    portfolio = data[perfil_nome]
    pesos_dict = portfolio.get("pesos", {})

    if not pesos_dict:
        aba.info(f"Carteira '{perfil_nome}' não possui ativos alocados.")
        continue

    tickers_carteira = list(pesos_dict.keys())
    
    df_info_ativos, df_precos = get_data_for_dashboard(db_conn, tickers_carteira)

    with aba:
        df_pesos = pd.DataFrame(pesos_dict.items(), columns=['Ativo', 'Peso'])
        
        if not df_info_ativos.empty:
            df_merged_total = pd.merge(df_pesos, df_info_ativos, left_on='Ativo', right_on='TICKER')
        else:
            df_merged_total = df_pesos.copy()
            df_merged_total['CATEGORIA'] = 'Não Categorizado'
            df_merged_total['SEGMENTO'] = 'Não Classificado'
        
        st.markdown("#### Filtros da Carteira")
        
        all_categories = sorted(df_merged_total['CATEGORIA'].unique().tolist())
        all_segments = sorted(df_merged_total['SEGMENTO'].unique().tolist())

        filt_col1, filt_col2 = st.columns(2)
        
        with filt_col1:
            selected_categories = st.multiselect(
                'Filtrar por Categoria',
                options=all_categories,
                default=all_categories,
                key=f"cat_filter_{perfil_nome}"
            )
        
        with filt_col2:
            selected_segments = st.multiselect(
                'Filtrar por Segmento',
                options=all_segments,
                default=all_segments,
                key=f"seg_filter_{perfil_nome}"
            )
        
        df_merged_filtrado = df_merged_total[
            (df_merged_total['CATEGORIA'].isin(selected_categories)) &
            (df_merged_total['SEGMENTO'].isin(selected_segments))
        ]
        
        col_comp, col_div, col_seg = st.columns(3)

        with col_comp:
            st.subheader("Composição por Ativo")
            fig_comp = px.treemap(
                df_merged_filtrado,
                path=[px.Constant("Carteira"), 'Ativo'],
                values='Peso',
                title='Alocação por Ativo (Filtrado)',
                custom_data=['Peso']
            )
            fig_comp.data[0].textinfo = "label+percent root"
            fig_comp.update_traces(
                texttemplate="%{label}<br>%{value:.1%}",
                hovertemplate="<b>%{label}</b><br>Peso na Carteira: %{customdata[0]:.2%}<extra></extra>"
            )
            st.plotly_chart(fig_comp, use_container_width=True, key=f"comp_{perfil_nome}")

        with col_div:
            st.subheader("Diversificação por Categoria")
            
            if not df_merged_filtrado.empty:
                df_categoria_agregado = df_merged_filtrado.groupby('CATEGORIA')['Peso'].sum().reset_index()
                
                fig_div = px.pie(
                    df_categoria_agregado,
                    values='Peso',
                    names='CATEGORIA',
                    title='Alocação por Categoria (Filtrado)'
                )
                fig_div.update_traces(
                    textposition='inside',
                    texttemplate='%{percent:.1%}',
                    hovertemplate="<b>%{label}</b><br>Peso: %{value:.2%}<extra></extra>"
                )
                st.plotly_chart(fig_div, use_container_width=True, key=f"div_{perfil_nome}")
            else:
                st.info("Nenhum dado de categoria para os filtros selecionados.")

        with col_seg:
            st.subheader("Diversificação por Segmento")
            
            if not df_merged_filtrado.empty:
                df_segmento_agregado = df_merged_filtrado.groupby('SEGMENTO')['Peso'].sum().reset_index()
                
                fig_seg = px.pie(
                    df_segmento_agregado,
                    values='Peso',
                    names='SEGMENTO',
                    title='Alocação por Segmento (Filtrado)'
                )
                fig_seg.update_traces(
                    textposition='inside',
                    texttemplate='%{percent:.1%}',
                    hovertemplate="<b>%{label}</b><br>Peso: %{value:.2%}<extra></extra>"
                )
                st.plotly_chart(fig_seg, use_container_width=True, key=f"seg_{perfil_nome}")
            else:
                st.info("Nenhum dado de segmento para os filtros selecionados.")
        
        st.divider()

        if not df_precos.empty:
            df_backtest = create_backtest_df(df_precos, pesos_dict)
            
            if not df_backtest.empty:
                st.subheader("Rendimento Total no Período (3 Meses)")
                retorno_total_3m = (df_backtest['Carteira (Base 100)'].iloc[-1] / df_backtest['Carteira (Base 100)'].iloc[0]) - 1
                
                st.metric(
                    label=f"Rendimento Total da Carteira",
                    value=f"{retorno_total_3m * 100:.2f}%"
                )

                st.divider()

                st.subheader("Rendimento Mensal (%)")
                try:
                    df_mensal = df_backtest['Carteira (Base 100)'].resample('ME').last()
                    df_retorno_mensal = df_mensal.pct_change().dropna() * 100
                    if not df_retorno_mensal.empty:
                        df_retorno_mensal.name = 'Rendimento Mensal (%)'
                        df_retorno_mensal.index = df_retorno_mensal.index.strftime('%Y-%m')
                        st.bar_chart(df_retorno_mensal, use_container_width=True)
                    else:
                        st.info("Dados insuficientes para gerar um gráfico de retorno mensal.")
                except Exception as e:
                    st.warning(f"Não foi possível calcular o rendimento mensal: {e}")
                
                st.divider()
                st.subheader("Rendimento Individual dos Ativos (%)")
                
                df_individual_raw = create_individual_return_df(df_precos, pesos_dict)
                
                if not df_individual_raw.empty:
                    available_tickers = df_merged_filtrado['Ativo'].tolist()
                    all_tickers_with_data = df_individual_raw.columns.tolist()
                    options_for_multiselect = [
                        t for t in all_tickers_with_data if t in available_tickers
                    ]
                    
                    selected_tickers = st.multiselect(
                        'Selecione os ativos para visualizar (baseado nos filtros acima)',
                        options=options_for_multiselect,
                        default=options_for_multiselect,
                        key=f"individual_filter_{perfil_nome}"
                    )

                    if selected_tickers:
                        df_individual_filtered = df_individual_raw[selected_tickers]
                        
                        df_plot = df_individual_filtered.reset_index().melt(
                            id_vars=df_individual_filtered.index.name,
                            var_name='Ativo',
                            value_name='Rendimento (%)'
                        )

                        fig_individual = px.line(
                            df_plot,
                            x=df_individual_filtered.index.name,
                            y='Rendimento (%)',
                            color='Ativo',
                            title=f"Rendimento Individual dos Ativos ({perfil_nome.capitalize()})",
                            labels={'DATA': 'Data', 'Rendimento (%)': 'Rendimento (%)'}
                        )
                        
                        fig_individual.update_layout(
                            hovermode="x unified",
                            yaxis_tickformat=".2f",
                            xaxis=dict(
                                rangeselector=dict(
                                    buttons=list([
                                        dict(count=1, label="1m", step="month", stepmode="backward"),
                                        dict(count=3, label="3m", step="month", stepmode="backward"),
                                        dict(step="all")
                                    ])
                                ),
                                rangeslider=dict(visible=True),
                                type="date"
                            )
                        )

                        fig_individual.update_traces(
                            hovertemplate="<b>%{x|%d %b %Y}</b><br>Rendimento: %{y:.2f}%<extra></extra>"
                        )

                        st.plotly_chart(fig_individual, use_container_width=True, key=f"individual_chart_{perfil_nome}")
                    else:
                        st.info("Nenhum ativo selecionado para visualização (verifique os filtros).")
                else:
                    st.info("Não foi possível calcular o rendimento individual dos ativos.")
                
            else:
                st.info("Dados de preço insuficientes para calcular o rendimento histórico da carteira.")
        else:
            st.info("Dados de preço insuficientes para calcular o rendimento histórico.")
