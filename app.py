import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import zipfile
import copy
import re

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Motor Alocação IFSC v22.2 (Fix)", layout="wide")
st.title("🧩 Motor de Alocação IFSC - V22.2 (Correção EAD)")
st.markdown("""
**Correções V22.2:**
1.  **Fix Bug 'dia':** Corrigido erro técnico na alocação de UCs EAD.
2.  **Lógica Sólida:** Mantém a alocação contínua e prioridade de Labs.
""")

# --- CONSTANTES ---
DIAS = ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira', 'Quinta-Feira', 'Sexta-Feira']
CURSOS_SEM_SEXTA = ['EVENTOS', 'GUIA REGIONAL', 'GUIA NACIONAL']
LABS_AB = [
    "Lab. Panificação", "Lab. Confeitaria", "Lab. Habilidades", 
    "Lab. Produção", "Lab. Cozinha Regional", "Lab. Bebidas", "Lab. Panif/Conf"
]
SALAS_TEORICAS = [f"Sala {i}" for i in range(1, 13) if i != 6]
SALAS_BACKUP = ["Lab. Informática 1", "Lab. Informática 2", "Restaurante 1"]

# --- FUNÇÕES AUXILIARES ---
def gerar_template():
    df = pd.DataFrame(columns=[
        "ID_Turma", "Nome_UC", "Turno", "Docentes", "Espacos", 
        "Tipo_Alocacao", "Carga_Horaria_Total", "Regra_Especial", 
        "Dia_Travado", "Semana_Inicio", "Semana_Fim"
    ])
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Demandas', index=False)
    return buffer

def converter_csv(df):
    return df.to_csv(index=False).encode('utf-8-sig')

# --- CLASSE DO MOTOR ---
class MotorAlocacao:
    def __init__(self, df_demandas, df_docentes):
        self.demandas = df_demandas.fillna("")
        self.restricoes = df_docentes.fillna("")
        self.grade = []
        self.erros = []
        self.matriz = {} 
        self.sala_base = {} 

    def normalizar(self, texto):
        return str(texto).strip().upper()

    def verificar_conflito(self, recursos, dia, turno, sem_ini, sem_fim):
        for rec in recursos:
            rec_norm = self.normalizar(rec)
            for sem in range(sem_ini, sem_fim + 1):
                chave = f"{rec_norm}|{dia}|{turno}|{sem}"
                if self.matriz.get(chave): return True
        return False

    def reservar(self, recursos, dia, turno, sem_ini, sem_fim):
        for rec in recursos:
            rec_norm = self.normalizar(rec)
            for sem in range(sem_ini, sem_fim + 1):
                chave = f"{rec_norm}|{dia}|{turno}|{sem}"
                self.matriz[chave] = True

    def verificar_bloqueio_docente(self, docente, dia, turno, sem_ini, sem_fim):
        try:
            regra = self.restricoes[self.restricoes['Nome_Docente'] == docente]
            if not regra.empty:
                dias_indisp = str(regra.iloc[0]['Dias_Indisponiveis'])
                if dia in dias_indisp and turno in dias_indisp: return True
                
                # Bloqueio Semanal
                if 'Bloqueio_Semana_Inicio' in regra.columns:
                    b_ini = int(regra.iloc[0]['Bloqueio_Semana_Inicio'] or 0)
                    b_fim = int(regra.iloc[0]['Bloqueio_Semana_Fim'] or 0)
                    if b_ini > 0 and b_fim > 0:
                        # Se houver sobreposição com o bloqueio
                        if not (sem_fim < b_ini or sem_ini > b_fim): return True
        except: pass
        return False

    def otimizar_dados_entrada(self):
        # Fusão de UCs fragmentadas (ex: Parte 1, 2)
        df = self.demandas.copy()
        def limpar_nome(nome):
            return re.sub(r'\s*\(parte \d+\)', '', str(nome), flags=re.IGNORECASE).strip()
        df['Nome_Base'] = df['Nome_UC'].apply(limpar_nome)
        
        # Agrupa apenas se for da mesma turma e tiver mesmo nome base
        grupos = df.groupby(['ID_Turma', 'Nome_Base'])
        novas_demandas = []
        
        for (turma, nome), grupo in grupos:
            if len(grupo) > 1 and "PROEJA" in str(turma).upper(): 
                # Fusão para PROEJA (geralmente sequencial)
                ch_total = grupo['Carga_Horaria_Total'].sum()
                if ch_total > 80: ch_total = 80
                
                docentes = ", ".join(grupo['Docentes'].unique())
                espacos = " + ".join(grupo['Espacos'].unique())
                # Remove duplicatas na string de espaços
                espacos = " + ".join(list(set([e.strip() for e in espacos.split('+')])))
                
                item = grupo.iloc[0].to_dict()
                item['Nome_UC'] = nome 
                item['Carga_Horaria_Total'] = ch_total
                item['Docentes'] = docentes
                item['Espacos'] = espacos
                item['Tipo'] = "FUSAO"
                novas_demandas.append(item)
            else:
                for _, row in grupo.iterrows():
                    row['Tipo'] = "SIMPLE"
                    novas_demandas.append(row.to_dict())
        return novas_demandas

    def definir_sala_base(self):
        """
        Define uma Sala Teórica fixa para cada turma regular.
        Ignora turmas que só têm 'sem sala' (Idiomas).
        """
        turmas = self.demandas['ID_Turma'].unique()
        turmas_com_sala = []
        
        # Filtra turmas que realmente precisam de sala física
        for t in turmas:
            ucs = self.demandas[self.demandas['ID_Turma'] == t]
            precisa_sala = False
            for _, row in ucs.iterrows():
                if "SEM SALA" not in str(row['Espacos']).upper() and "EAD" not in str(row['Espacos']).upper():
                    precisa_sala = True
                    break
            if precisa_sala:
                turmas_com_sala.append(t)
        
        # Distribui salas (Round Robin simples por enquanto)
        idx = 0
        for t in turmas_com_sala:
            if idx < len(SALAS_TEORICAS):
                self.sala_base[t] = SALAS_TEORICAS[idx]
                idx += 1
            else:
                # Backup
                self.sala_base[t] = SALAS_BACKUP[idx % len(SALAS_BACKUP)]
                idx += 1

    def preparar_demandas(self):
        lista = self.otimizar_dados_entrada()
        # Ordenação: Labs primeiro -> Teóricas -> Sem Sala
        def peso(item):
            esp = str(item.get('Espacos', '')).upper()
            if "SEM SALA" in esp or "EAD" in esp: return 3
            if any(l.upper() in esp for l in map(str.upper, LABS_AB)): return 1
            return 2
        
        lista.sort(key=peso)
        return lista

    def alocar_item(self, item):
        # 1. Verifica se é Virtual ou EAD
        espacos_str = str(item.get('Espacos', '')).upper()
        if "EAD" in espacos_str or "100% EAD" in str(item.get('Regra_Especial', '')).upper():
            return (True, item['Carga_Horaria_Total'], {
                "rec": [str(item['ID_Turma'])], "sem_ini": 1, "sem_fim": 20, 
                "sala": "EAD", "obs": "EAD", 
                "dia": "EAD", "is_ead": True # CORREÇÃO AQUI: Adicionado chaves obrigatórias
            })
            
        eh_sem_sala = "SEM SALA" in espacos_str
        
        # 2. Configurações de Tempo
        ch_total = float(item['Carga_Horaria_Total'] or 0)
        duracao_semanas = int(np.ceil(ch_total / 4))
        
        # 3. Definição de Recursos Físicos
        recursos_fisicos = []
        if not eh_sem_sala:
            tem_lab = False
            for lab in LABS_AB:
                if lab.upper() in espacos_str:
                    recursos_fisicos.append(lab) 
                    tem_lab = True
            
            if not tem_lab:
                sala = self.sala_base.get(item['ID_Turma'])
                if sala: recursos_fisicos.append(sala)
            elif "SALA" in espacos_str: 
                 sala = self.sala_base.get(item['ID_Turma'])
                 if sala: recursos_fisicos.append(sala)

        # 4. Busca de Horário
        dias_possiveis = DIAS
        if item.get('Dia_Travado'): dias_possiveis = [item['Dia_Travado']]
        
        # Filtro Sexta
        eh_curso_sem_sexta = any(c in str(item['ID_Turma']).upper() for c in CURSOS_SEM_SEXTA)
        
        # Tenta alocar bloco contínuo
        for dia in dias_possiveis:
            if dia == 'Sexta-Feira' and eh_curso_sem_sexta: continue
            
            for inicio in range(1, 22 - duracao_semanas + 1):
                fim = inicio + duracao_semanas - 1
                
                # Verifica Docentes
                docs = [d.strip() for d in str(item['Docentes']).split(',')]
                if any(self.verificar_bloqueio_docente(d, dia, item['Turno'], inicio, fim) for d in docs): continue
                
                # Verifica Turma
                if self.verificar_conflito([str(item['ID_Turma'])], dia, item['Turno'], inicio, fim): continue
                
                # Verifica Recursos Físicos
                if recursos_fisicos:
                    if self.verificar_conflito(recursos_fisicos, dia, item['Turno'], inicio, fim): continue
                
                # Sucesso!
                rec_final = docs + [str(item['ID_Turma'])] + recursos_fisicos
                return (True, ch_total, {
                    "rec": rec_final, "sem_ini": inicio, "sem_fim": fim, 
                    "sala": " + ".join(recursos_fisicos) if recursos_fisicos else "Virtual/Sem Sala",
                    "dia": dia
                })
                
        # 5. Se falhar bloco contínuo, tenta Split (Tetris) - Apenas para 40h+
        if ch_total >= 40:
            metade_dur = int(duracao_semanas / 2)
            # Tenta achar 2 dias livres nas mesmas semanas
            for inicio in range(1, 22 - metade_dur + 1):
                fim = inicio + metade_dur - 1
                
                # Acha Dia 1
                dia1 = None
                for d in dias_possiveis:
                    if d == 'Sexta-Feira' and eh_curso_sem_sexta: continue
                    docs = [d.strip() for d in str(item['Docentes']).split(',')]
                    if any(self.verificar_bloqueio_docente(d, d, item['Turno'], inicio, fim) for d in docs): continue
                    if self.verificar_conflito([str(item['ID_Turma'])] + recursos_fisicos, d, item['Turno'], inicio, fim): continue
                    dia1 = d
                    break
                
                if dia1:
                    # Acha Dia 2 (diferente de Dia 1)
                    for d in dias_possiveis:
                        if d == dia1: continue
                        if d == 'Sexta-Feira' and eh_curso_sem_sexta: continue
                        
                        docs = [d.strip() for d in str(item['Docentes']).split(',')]
                        if any(self.verificar_bloqueio_docente(d, d, item['Turno'], inicio, fim) for d in docs): continue
                        if self.verificar_conflito([str(item['ID_Turma'])] + recursos_fisicos, d, item['Turno'], inicio, fim): continue
                        
                        # Sucesso Split Paralelo!
                        rec_final = docs + [str(item['ID_Turma'])] + recursos_fisicos
                        return (True, ch_total, {
                            "rec": rec_final, "sem_ini": inicio, "sem_fim": fim,
                            "sala": " + ".join(recursos_fisicos) if recursos_fisicos else "Virtual",
                            "dia": f"{dia1} e {d}", "is_split": True, "dias_split": [dia1, d]
                        })

        return (False, 0, None)

    def executar(self):
        self.definir_sala_base()
        fila = self.preparar_demandas()
        
        total = len(fila)
        bar = st.progress(0)
        
        for idx, item in enumerate(fila):
            sucesso, ch, config = self.alocar_item(item)
            
            if sucesso:
                # CORREÇÃO AQUI: Só reserva se não for EAD
                if not config.get('is_ead'):
                    if config.get('is_split'):
                        self.reservar(config['rec'], config['dias_split'][0], item['Turno'], config['sem_ini'], config['sem_fim'])
                        self.reservar(config['rec'], config['dias_split'][1], item['Turno'], config['sem_ini'], config['sem_fim'])
                    else:
                        self.reservar(config['rec'], config['dia'], item['Turno'], config['sem_ini'], config['sem_fim'])
                
                status = "✅ Alocado"
                if "Virtual" in config['sala']: status += " (Sem Sala/Virtual)"
                if config.get('is_split'): status += " (Split)"
                
                self.grade.append({
                    "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": ch,
                    "Dia": config.get('dia', 'EAD'), "Turno": item['Turno'], "Docentes": item['Docentes'],
                    "Espacos": config['sala'], "Semana_Inicio": config['sem_ini'], "Semana_Fim": config['sem_fim'],
                    "Status": status
                })
            else:
                self.erros.append(f"Falha ao alocar: {item['ID_Turma']} - {item['Nome_UC']}")
                self.grade.append({
                    "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": item['Carga_Horaria_Total'],
                    "Status": "❌ Não Alocado (Conflito Irresolvível)"
                })
            
            bar.progress((idx + 1) / total)
            
        return pd.DataFrame(self.grade), self.erros

# --- INTERFACE ---
st.sidebar.header("📂 Área de Trabalho")
st.sidebar.download_button("📥 Baixar Modelo", gerar_template(), "modelo.xlsx")
st.sidebar.markdown("---")
up = st.sidebar.file_uploader("Upload Planilha", type=['xlsx'])

if up and st.button("🚀 Rodar Otimizador V22.2"):
    try:
        df_dem = pd.read_excel(up, sheet_name='Demandas')
        try: df_doc = pd.read_excel(up, sheet_name='Docentes')
        except: df_doc = pd.DataFrame()
        
        motor = MotorAlocacao(df_dem, df_doc)
        df_res, erros = motor.executar()
        
        st.success("Alocação Finalizada!")
        
        buf = BytesIO()
        with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED, False) as z:
            z.writestr("01_Grade_Geral.csv", converter_csv(df_res))
            z.writestr("02_Erros.csv", converter_csv(pd.DataFrame(erros, columns=["Erro"])))
            z.writestr("05_Dados_Brutos.json", df_res.to_json(orient='records', indent=4))
            
            if not df_res.empty:
                rows = []
                for _, row in df_res[df_res['Status'].str.contains("Alocado", na=False)].iterrows():
                    for d in str(row['Docentes']).split(','):
                        rows.append(row.to_dict() | {"Docente_Individual": d.strip()})
                z.writestr("04_Agenda_Docentes.csv", converter_csv(pd.DataFrame(rows)))
                z.writestr("03_Ocupacao_Espacos.csv", converter_csv(df_res[['Dia', 'Turno', 'Espacos', 'ID_Turma', 'Semana_Inicio', 'Semana_Fim']]))

        st.download_button("📦 Baixar Resultados (ZIP)", buf.getvalue(), "Resultados_V22.2.zip", "application/zip")
        st.dataframe(df_res)
        
    except Exception as e:
        st.error(f"Erro: {e}")
