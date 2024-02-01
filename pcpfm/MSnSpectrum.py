import os 

class MS2Spectrum():
    def __init__(self,
                 id,
                 precursor_mz, 
                 precursor_rt,
                 list_mz=None,
                 list_intensity=None,
                 matchms_spectrum=None,
                 source='',
                 instrument=None,
                 collision_energy=None,
                 compound_name=None,
                 annotations=None):
        #super().__init__(id)
        source = os.path.basename(source) if source != '' else os.path.basename(source)
        self.precursor_ion = str(precursor_mz) + "_" + str(precursor_rt) + "_" + os.path.basename(source)
        self.retention_time = precursor_rt
        self.precursor_ion_mz = precursor_mz
        self.list_mz = list_mz if list_mz is not None else []
        self.list_intensity = list_intensity if list_intensity is not None else []
        self.instrument = instrument
        self.collision_energy = collision_energy
        self.matchms_spectrum = matchms_spectrum
        if self.matchms_spectrum:
            self.list_mz = [x[0] for x in self.matchms_spectrum.peaks]
            self.list_intensity = [x[1] for x in self.matchms_spectrum.peaks]
        self.annotations = [] if annotations is None else annotations
        self.compound_name = compound_name
        self.source = source
    
    @staticmethod
    def from_embedding(embedding):
        from matchms.Spectrum import Spectrum
        import numpy as np
        return MS2Spectrum(
            id = None,
            precursor_mz = embedding["precursor_ion_mz"],
            precursor_rt = embedding['retention_time'],
            list_mz=embedding['list_mz'],
            list_intensity=embedding['list_intensity'],
            matchms_spectrum=Spectrum(mz = np.array(list(embedding['list_mz'])), 
                                        intensities=np.array(embedding['list_intensity']),
                                        metadata={}),
            source=embedding['source'],
            instrument=embedding['instrument'],
            collision_energy=embedding['collision_energy'],
            compound_name=None,
            annotations=embedding['annotations']
        )


    def annotate(self, other_MS2, score, matched_peaks, annotation_level="Unspecified"):
        self.annotations.append(
            {
                "msms_score": score,
                "matched_peaks": matched_peaks,
                "db_precursor_mz": other_MS2.precursor_ion_mz,
                "reference_id": other_MS2.compound_name,
                "list_mz": [x[0] for x in other_MS2.matchms_spectrum.peaks],
                "list_intensity": [x[1] for x in other_MS2.matchms_spectrum.peaks],
                "annot_source": other_MS2.source,
                "annotation_level": annotation_level
            }
        )

    def embedding(self):
        embedding = {}
        for k, v in self.__dict__.items():
            if type(v) in [int, float, str, dict, set, list]:
                embedding[k] = v
        return embedding