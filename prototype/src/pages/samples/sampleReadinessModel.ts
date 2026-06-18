import { dataLineage, mappingGaps, sampleImportMethods, sampleMappingMatrix } from '../../data/mockData';

export function getSampleImportViewModel() {
  return {
    methods: sampleImportMethods,
    mappingMatrix: sampleMappingMatrix,
    mappingGaps,
    dataLineage,
  };
}
