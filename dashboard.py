import streamlit as st
import pandas as pd
import json
import plotly.express as px
from datetime import datetime, timedelta
# from config import DIRETORIO_ARQUIVO, DIRETORIO_PROJETO -> Rodando no desktop
import os

DIRETORIO_ARQUIVO = "data"
DIRETORIO_PROJETO = None

st.set_page_config(layout="wide", page_title="Dashboard de Carteiras")

# --- Funções de Busca de Dados ---

@st.cache_data(ttl=3600)
def load_excel_data(diretorio):
    try:
        # Construindo os caminhos completos com os arquivos em CAPS LOCK
        caminho_ativos = os.path.join(diretorio, "ATIVOS.xlsx")
        caminho_categorias = os.path.join(diretorio, "CATEGORIAS.xlsx")
        caminho_precos = os.path.join(diretorio, "PRECOS.xlsx")

        df_ativos = pd.read_excel(caminho_ativos)
        df_categorias = pd.read_excel(caminho_categorias)
        df_precos = pd.read_excel(caminho_precos)
        
        # Otimização: Converter a coluna DATA para datetime logo na carga
        if 'DATA' in df_precos.columns:
            df_precos['DATA'] = pd.to_datetime(df_precos['DATA'])
            
        return df_ativos, df_categorias, df_precos
    except FileNotFoundError as e:
        st.error(f"Erro: Arquivo Excel não encontrado no diretório '{diretorio}'. Verifique os caminhos. Detalhe: {e}")
        return None, None, None
    except Exception as e:
        st.error(f"Erro ao carregar arquivos Excel: {e}")
        return None, None, None

@st.cache_data(ttl=600)
def load_json_data(diretorio=None, filepath="carteiras_otimizadas.json"):
    # Também ajustei o JSON para buscar na mesma pasta, caso ele fique lá!
    #caminho_completo = os.path.join(diretorio, filepath) - Rodando no desktop
    caminho_completo = filepath
    try:
        with open(caminho_completo, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        st.error(f"Erro: Arquivo '{filepath}' não encontrado no diretório.")
        st.info("Por favor, execute o script principal de cálculo de carteira primeiro.")
        return None
    except Exception as e:
        st.error(f"Erro ao ler o arquivo JSON: {e}")
        return None

@st.cache_data(ttl=3600)
def get_data_for_dashboard(df_ativos, df_categorias, df_precos, tickers_carteira: list):
    if not tickers_carteira or df_ativos is None:
        return pd.DataFrame(columns=['TICKER', 'SEGMENTO', 'CATEGORIA']), pd.DataFrame()
    
    # 1. Processar Informações dos Ativos
    df_ativos_filtrados = df_ativos[df_ativos['TICKER'].isin(tickers_carteira)].copy()
    
    # Fazendo o merge (join) entre ATIVOS e CATEGORIAS
    if not df_categorias.empty and 'id' in df_categorias.columns:
        df_info_ativos = pd.merge(
            df_ativos_filtrados, 
            df_categorias, 
            left_on='CATEGORIA', 
            right_on='id', 
            how='left',
            suffixes=('_ativo', '')
        )
    else:
        df_info_ativos = df_ativos_filtrados.copy()

    # Garantir que as colunas existam antes de selecionar
    for col in ['TICKER', 'SEGMENTO', 'CATEGORIA']:
        if col not in df_info_ativos.columns:
            df_info_ativos[col] = 'Não Classificado'

    df_info_ativos = df_info_ativos[['TICKER', 'SEGMENTO', 'CATEGORIA']].copy()
    
    # Preencher vazios
    df_info_ativos['CATEGORIA'] = df_info_ativos['CATEGORIA'].fillna('Não Categorizado')
    df_info_ativos['SEGMENTO'] = df_info_ativos['SEGMENTO'].fillna('Não Classificado')

    # 2. Processar Preços Históricos
    df_precos_filtrados = df_precos[df_precos['TICKER'].isin(tickers_carteira)].copy()
    
    data_limite = datetime.now() - timedelta(days=180)
    df_precos_filtrados = df_precos_filtrados[df_precos_filtrados['DATA'] >= data_limite]
    
    if df_precos_filtrados.empty:
        st.warning("Nenhum dado de preço encontrado nos últimos 6 meses.")
        return df_info_ativos, pd.DataFrame()
        
    try:
        df_precos_pivot = df_precos_filtrados.pivot(
            index='DATA', columns='TICKER', values='PRECO'
        )
        df_precos_pivot = df_precos_pivot.sort_index()
        df_precos_pivot = df_precos_pivot.ffill().dropna(axis=1, how='all') 
        
        return df_info_ativos, df_precos_pivot

    except Exception as e:
        st.error(f"Erro ao processar preços históricos: {e}")
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

# Carregar Dados (passando o diretório como argumento)
df_ativos, df_categorias, df_precos = load_excel_data(DIRETORIO_ARQUIVO)
data = load_json_data(DIRETORIO_PROJETO)

# Interrompe se dados não carregaram
if df_ativos is None or data is None:
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
    
    # Buscar os dados passando os DataFrames do Excel que carregamos
    df_info_ativos, df_precos_pivot = get_data_for_dashboard(
        df_ativos, df_categorias, df_precos, tickers_carteira
    )

    with aba:
        # 1. Criar o df_pesos
        df_pesos = pd.DataFrame(pesos_dict.items(), columns=['Ativo', 'Peso'])
        
        # 2. Criar o DataFrame mergeado principal (usado em todos os gráficos de composição)
        if not df_info_ativos.empty:
            df_merged_total = pd.merge(df_pesos, df_info_ativos, left_on='Ativo', right_on='TICKER')
        else:
            df_merged_total = df_pesos.copy()
            df_merged_total['CATEGORIA'] = 'Não Categorizado'
            df_merged_total['SEGMENTO'] = 'Não Classificado'
        
        # --- FILTROS GLOBAIS NO TOPO DA ABA ---
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
        
        # 3. Aplicar os filtros para criar um DataFrame filtrado
        df_merged_filtrado = df_merged_total[
            (df_merged_total['CATEGORIA'].isin(selected_categories)) &
            (df_merged_total['SEGMENTO'].isin(selected_segments))
        ]
        
        col_comp, col_div, col_seg = st.columns(3)

        # --- Coluna 1 (Treemap por Ativo) ---
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

        # --- Coluna 2 (Pizza por Categoria) ---
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
        
        # --- Coluna 3 (Pizza por Segmento) ---
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

        # --- Seção de Rendimento ---
        if not df_precos_pivot.empty:
            df_backtest = create_backtest_df(df_precos_pivot, pesos_dict) 
            
            if not df_backtest.empty:
                st.subheader("Rendimento Total no Período (3 Meses)")
                retorno_total_6m = (df_backtest['Carteira (Base 100)'].iloc[-1] / df_backtest['Carteira (Base 100)'].iloc[0]) - 1

                st.metric(
                    label=f"Rendimento Total da Carteira", 
                    value=f"{retorno_total_6m * 100:.2f}%"
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
                
                # --- Seção de Rendimento Individual ---
                st.divider()
                st.subheader("Rendimento Individual dos Ativos (%)")
                
                df_individual_raw = create_individual_return_df(df_precos_pivot, pesos_dict)
                
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