from fpdf import FPDF
import datetime
import json
import textwrap
import os

HEADER = 'PCPFM Report - '

class ReportPDF(FPDF):
    def header(self):
        global HEADER
        self.set_font('Arial', 'B', 15)
        self.cell(80)
        self.cell(30, 10, HEADER, 0, 0, 'C')
        self.ln(10)

    def footer(self):
        self.set_y(15)
        self.set_font('Arial', 'I', 8)


class Report():
    default_style = json.load(open("/Users/mitchjo/Projects/PythonCentricPipelineForMetabolomics-1/pcpfm/report_templates/jmm_default.json"))
    for section in default_style["sections"]:
        if "text" in section:
            section["text"] = default_style["texts"][section["text"]]
        else:
            section["text"] = None
    default_font = ['Arial', '', 12]

    def __init__(self, experiment, style=default_style) -> None:
        global HEADER
        self.experiment = experiment
        HEADER += self.experiment.experiment_directory.split("/")[-1]
        self.report = self.initialize_report()
        self.max_width = round(self.report.line_width * 1000,0)
        
        for section in style["sections"]:
            self.__getattribute__(section["section"])(section)
            self.end_section()

    def all_TICs(self, section_desc):
        for acquisition in self.experiment.acquisitions:
            tic_path = acquisition.TICz()
            self.report.image(tic_path, w=self.max_width)


    def initialize_report(self):
        report = ReportPDF()
        report.add_page()
        report.set_font(self.default_font[0], self.default_font[1], self.default_font[2])
        return report

    def reset_font(self):
        self.report.set_font(self.default_font[0], self.default_font[1], self.default_font[2])

    def section_head(self, title):
        self.report.cell(80)
        self.report.set_font(self.default_font[0], 'B', self.default_font[2])
        self.report.cell(30, 10, title, 0, 0, 'C')
        self.reset_font()
        self.report.ln(5)

    def subsection_head(self, title):
        self.report.cell(80)
        self.report.cell(30, 10, title, 0, 0, 'C')
        self.report.ln(5)

    def section_line(self, content, options=None):
        options = set(options) if options is not None else set()
        if "bold" in options:
            self.report.set_font(self.default_font[0], 'B', self.default_font[2])
            self.report.cell(30, 10, content, 0, 0, "B")
            self.reset_font()
        else:
            self.report.cell(30, 10, content, 0, 0)
        self.report.ln(5)

    def section_text(self, text, options=None):
        text = ' '.join(text.split(None))
        text = ' '.join(text.split("\n"))
        for txt in textwrap.wrap(text, self.max_width / 2):
            self.section_line(txt, options=options)
        self.report.ln(5)

    def end_section(self):
        self.report.ln(5)

    def experiment_summary(self, text=None):
        pass

    def annotation_summary(self, section_desc):
        self.section_head("Annotation Summary")
        if 'text' in section_desc: 
            self.section_text(section_desc['text'])
        self.subsection_head("Feature Tables")
        self.section_line("Table Name, # Features, # MS1 Annotated Features, # MS2 Annotated Features", options=["bold"])
        for table in self.experiment.feature_tables.keys():
            feature_table = self.experiment.retrieve(table, True, False, True)
            num_features = feature_table.num_features
            if "ms2_annotations" in feature_table.feature_table.columns:
                ms2_annotated_features = [x for x in feature_table["ms2_annotations"] if x]
                num_ms2_annotations = len(ms2_annotated_features)
            else:
                num_ms2_annotations = 0

            if "ms1_annotations" in feature_table.feature_table.columns:
                ms1_annotated_features = [x for x in feature_table["ms1_annotations"] if x and x != '[]']
                num_ms1_annotations = len(ms1_annotated_features)
            else:
                num_ms1_annotations = 0

            self.section_line(", ".join([str(x) for x in [table, num_features, num_ms1_annotations, num_ms2_annotations]]))
        self.section_head("")
        self.subsection_head("Empirical Compounds")
        self.section_line("empCpd Name, # Khipus, # MS1 Annotated Khipus, # MS2 Annotated Khipus", options=["bold"])
        for empcpd in self.experiment.empCpds.keys():
            empcpd_object = self.experiment.retrieve(empcpd, False, True, True)
            num_annotated_ms1 = 0
            num_annotated_ms2 = 0
            total = 0
            for kp_id, khipu in empcpd_object.dict_empCpds.items():
                if "mz_only_db_matches" in khipu and khipu["mz_only_db_matches"]:
                    num_annotated_ms1 += 1
                if "MS2_Spectra" in khipu and khipu["MS2_Spectra"] and "Annotations" in khipu["MS2_Spectra"]:
                    num_annotated_ms2 += 1
                total += 1
            self.section_line(", ".join([str(x) for x in [empcpd, total, num_annotated_ms1, num_annotated_ms2]]))

    def table_summary(self, section_desc):
        self.section_head("Feature Table Summary")
        if 'text' in section_desc: 
            self.section_text(section_desc['text'])
        self.section_line("Table Name, Num Samples, Num Features", options=["bold"])
        for table in self.experiment.feature_tables.keys():
            feature_table = self.experiment.retrieve(table, True, False, True)
            self.section_line(", ".join([str(x) for x in [table, feature_table.num_samples, feature_table.num_features]]))

    def empcpd_summary(self, section_desc):
        self.section_head("empCpd Table Summary")
        if 'text' in section_desc: 
            self.section_text(section_desc['text'])
        self.section_line("EmpCpd Name, Num Khipus, Num Features", options=["bold"])
        for empcpd in self.experiment.empCpds.keys():
            empcpd_object = self.experiment.retrieve(empcpd, False, True, True)
            self.section_line(", ".join([str(x) for x in [empcpd, empcpd_object.num_khipus, empcpd_object.num_features]]))

    def timestamp(self, section_desc):
        timestamp_string = 'Report generated on ' + str(datetime.datetime.now())
        self.section_head("Timestamp")
        self.section_line(timestamp_string)

    def save(self, section_desc):
        if not section_desc["report_name"].endswith(".pdf"):
            section_desc["report_name"] = section_desc["report_name"] + ".pdf"
        out_path = os.path.join(os.path.abspath(self.experiment.experiment_directory), "reports", section_desc["report_name"])
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        self.report.output(os.path.join(os.path.abspath(self.experiment.experiment_directory), "reports", section_desc["report_name"]))

    def figure(self, section_desc):
        self.report.add_page()
        self.section_line("Table: " + section_desc["table"])
        self.report.ln(10)
        figure_path = os.path.join(os.path.abspath(self.experiment.experiment_directory), "QAQC_figs/" + section_desc["table"], section_desc["name"])
        self.report.image(figure_path, w=self.max_width)